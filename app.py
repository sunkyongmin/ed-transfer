"""
동신병원 응급실 전원병원 선정 도우미
- 환자 병력·검사결과 → 규칙 기반 진단추정(외부 전송 없음) → 중증질환 분류 → 수용가능 전원병원 후보
- 국립중앙의료원 전국 응급의료기관 정보 조회 서비스(공공데이터포털) 실시간 데이터 사용
- 실행: streamlit run app.py
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import nemc_api as api
import transfer_log as tlog
import triage_rules as tr

st.set_page_config(page_title="전원병원 선정 도우미", page_icon="🚑", layout="wide")

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"          # 인증키 저장 위치 (앱 폴더)
# 기준 병원 초기값 (사이드바에서 변경·저장 가능 → config.json)
DEFAULT_ORIGIN = {"hpid": "", "name": "동신병원", "sido": "서울특별시", "lat": 37.5733, "lon": 126.9289}

# 검색 지역: 표시명 → (시도명 후보들, 시군구 목록 또는 None)
# 시군구가 None이면 시도 전체를 한 번에 조회하고, 결과가 비면 기관 주소에서 뽑은 시군구 단위로 재시도
SIDO_NAMES: dict[str, list[str]] = {
    "서울특별시": ["서울특별시"], "부산광역시": ["부산광역시"], "대구광역시": ["대구광역시"], "인천광역시": ["인천광역시"],
    "전남광주통합특별시": ["전남광주통합특별시", "광주광역시", "전라남도"],
    "대전광역시": ["대전광역시"], "울산광역시": ["울산광역시"],
    "세종특별자치시": ["세종특별자치시", "세종시"], "경기도": ["경기도"],
    "강원특별자치도": ["강원특별자치도", "강원도"], "충청북도": ["충청북도"], "충청남도": ["충청남도"],
    "전북특별자치도": ["전북특별자치도", "전라북도"], "경상북도": ["경상북도"], "경상남도": ["경상남도"],
    "제주특별자치도": ["제주특별자치도", "제주도"],
}
REGIONS: dict[str, tuple[str, list[str] | None]] = {f"{sido} 전체": (sido, None) for sido in SIDO_NAMES}
# 서울 인접 경기·인천 시군구는 세부 단위로도 고를 수 있게 추가
for _sido, _cities in {
    "경기도": ["고양시", "부천시", "광명시", "과천시", "안양시", "성남시", "구리시", "하남시", "김포시", "의정부시",
             "남양주시", "수원시", "용인시", "파주시", "안산시", "시흥시", "군포시", "의왕시", "양주시", "동두천시"],
    "인천광역시": ["부평구", "계양구", "서구", "남동구", "미추홀구", "연수구", "중구", "동구", "강화군"],
}.items():
    for _c in _cities:
        REGIONS[f"{_sido[:2]} {_c}"] = (_sido, [_c])

RESOURCE_LABELS = {**api.EQUIP_FIELDS, **{k: label for k, _, label in api.BED_FIELDS}}


# ---------------------------------------------------------------------------
# 인증키 저장/불러오기
# ---------------------------------------------------------------------------
def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config(**updates) -> bool:
    cfg = load_config(); cfg.update(updates)
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_saved_key() -> str:
    k = load_config().get("NEMC_API_KEY", "")
    if k:
        return k
    try:
        k = st.secrets.get("NEMC_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("NEMC_API_KEY", "")


def save_key(key: str) -> bool:
    return save_config(NEMC_API_KEY=key)


# ---------------------------------------------------------------------------
# 캐시된 API 조회
# ---------------------------------------------------------------------------
def _sigungu_from_addrs(hosp: dict) -> list[str]:
    """기관 주소의 두 번째 토큰(시군구)을 모아 재시도용 목록 생성."""
    out = []
    for h in hosp.values():
        parts = (h.addr or "").split()
        if len(parts) >= 2:
            g = parts[1]
            if len(parts) >= 3 and parts[2].endswith("구") and parts[1].endswith("시"):
                g = f"{parts[1]} {parts[2]}"   # 예: 고양시 덕양구
            if g not in out:
                out.append(g)
    return out


def _fetch_region(fetch_fn, key, region_name: str, hosp_cache: dict | None = None) -> dict:
    """시도 전체 → (비면) 시군구 단위로 재시도. 시도명은 신명칭/구명칭 순으로 시도."""
    sido, sigungu = REGIONS[region_name]
    last_err = None
    for sido_name in SIDO_NAMES.get(sido, [sido]):
        attempts: list[list[tuple[str, str | None]]] = []
        if sigungu is None:
            attempts.append([(sido_name, None)])
            fallback = _sigungu_from_addrs(hosp_cache) if hosp_cache else []
            if fallback:
                attempts.append([(sido_name, g) for g in fallback])
        else:
            attempts.append([(sido_name, g) for g in sigungu])
        for attempt in attempts:
            merged: dict = {}
            try:
                for s_, g in attempt:
                    merged.update(fetch_fn(key, s_, g))
            except api.NEMCError as e:
                last_err = e
                if "게이트웨이" in str(e):
                    raise
                continue
            if merged:
                return merged
    if last_err:
        raise last_err
    return {}


MASTER_PATH = APP_DIR / "hospitals_master.csv"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_master(_mtime: float) -> dict[str, api.Hospital]:
    """hospitals_master.csv (build_master.py로 생성) → {hpid: Hospital}. 파일 없으면 {}."""
    if not MASTER_PATH.exists():
        return {}
    import csv
    out: dict[str, api.Hospital] = {}
    with MASTER_PATH.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat = float(r.get("lat") or ""); lon = float(r.get("lon") or "")
            except ValueError:
                lat = lon = None
            hpid = (r.get("hpid") or "").strip() or f"NAME:{r.get('name','').strip()}"
            out[hpid] = api.Hospital(hpid=hpid, name=r.get("name", ""), emcls=r.get("emcls", ""),
                                     emcls_name=r.get("emcls_name", ""), addr=r.get("addr", ""),
                                     tel_main=r.get("tel_main", ""), tel_er=r.get("tel_er", ""), lat=lat, lon=lon)
            out[hpid].sido = r.get("sido", "")  # 동적 속성
    return out


def _master_mtime() -> float:
    try:
        return MASTER_PATH.stat().st_mtime
    except OSError:
        return 0.0


def master_for_region(region_name: str) -> dict[str, api.Hospital]:
    """마스터 파일에서 지역(시도 전체 / 시군구)에 해당하는 기관만."""
    m = load_master(_master_mtime())
    if not m:
        return {}
    sido, sigungu = REGIONS[region_name]
    out = {}
    for hpid, h in m.items():
        h_sido = getattr(h, "sido", "") or ""
        if h_sido != sido and not any(h.addr.startswith(a) for a in SIDO_NAMES.get(sido, [sido])):
            continue
        if sigungu and not any(g.split()[0] in h.addr for g in sigungu):
            continue
        out[hpid] = h
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cached_hospital_list(key: str, region_names: tuple) -> dict:
    """기관 목록: 마스터 파일 우선, 없거나 비면 API. 빈 결과는 캐시하지 않도록 예외."""
    out: dict = {}
    for rn in region_names:
        got = master_for_region(rn)
        if not got:
            got = _fetch_region(api.fetch_hospital_list, key, rn)
        out.update(got)
    if not out:
        raise api.NEMCError(f"{', '.join(region_names)} 기관 목록이 비어 있습니다 (API 응답 없음, 마스터 파일 없음)")
    return out


@st.cache_data(ttl=60, show_spinner=False)
def cached_realtime(key: str, region_names: tuple):
    beds: dict = {}; severe: dict = {}; msgs: dict = {}
    for rn in region_names:
        hosp = cached_hospital_list(key, (rn,))  # 시군구 재시도용 (6시간 캐시)
        beds.update(_fetch_region(api.fetch_realtime_beds, key, rn, hosp))
        severe.update(_fetch_region(api.fetch_severe_acceptance, key, rn, hosp))
        try:
            for hp, lst in _fetch_region(api.fetch_messages, key, rn, hosp).items():
                msgs.setdefault(hp, []).extend(lst)
        except api.NEMCError:
            pass
    return beds, severe, msgs, tlog.now_kst().strftime("%Y-%m-%d %H:%M:%S")


def search_hospital(key: str, sido: str, name: str) -> tuple[list[dict], int]:
    """기준 병원 설정용: 시도 기관 목록(캐시)을 받아 앱 안에서 이름으로 필터. (결과, 시도 전체 기관 수)"""
    region = f"{sido} 전체"
    try:
        hosp = cached_hospital_list(key, (region,))
    except api.NEMCError:
        return [], 0
    q = name.replace(" ", "").lower()
    out = []
    for h in hosp.values():
        if q in (h.name or "").replace(" ", "").lower() and h.lat and h.lon:
            out.append({"hpid": h.hpid, "name": h.name, "addr": h.addr, "emcls": h.emcls_name,
                        "lat": h.lat, "lon": h.lon, "sido": sido})
    out.sort(key=lambda d: (len(d["name"]), d["name"]))
    return out, len(hosp)


def master_status() -> str:
    m = load_master(_master_mtime())
    if not m:
        return "마스터 파일 없음 — API 목록 사용 (python build_master.py 로 생성 권장)"
    import datetime as _dt
    return f"마스터 파일 {len(m)}개 기관 ({_dt.datetime.fromtimestamp(_master_mtime()).strftime('%Y-%m-%d')} 생성)"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cached_basic(key: str, hpid: str) -> dict:
    try:
        return api.fetch_basic_info(key, hpid)
    except api.NEMCError:
        return {}


# ---------------------------------------------------------------------------
# 사이드바: 인증키·검색조건
# ---------------------------------------------------------------------------
def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


# --- 비밀번호 (클라우드 배포 시 Secrets에 APP_PASSWORD 설정하면 활성화) ---
_pw = _secret("APP_PASSWORD")
if _pw and not st.session_state.get("authed"):
    st.title("🚑 응급실 전원병원 선정 도우미")
    entered = st.text_input("접속 비밀번호", type="password")
    if st.button("입장", type="primary") or entered:
        if entered == _pw:
            st.session_state.authed = True
            st.rerun()
        elif entered:
            st.error("비밀번호가 다릅니다.")
    st.stop()

# --- 인증키: Secrets(클라우드) > config.json/환경변수(로컬) ---
KEY_MANAGED = bool(_secret("NEMC_API_KEY"))
if "service_key" not in st.session_state:
    st.session_state.service_key = _secret("NEMC_API_KEY") or load_saved_key()

# --- 기준 병원: URL 파라미터(?hpid=…&sido=…) > 세션 > config.json > 기본값 ---
def _origin_from_query() -> dict | None:
    try:
        qp = st.query_params
        hpid, sido = qp.get("hpid", ""), qp.get("sido", "")
    except Exception:
        return None
    if not hpid or sido not in SIDO_NAMES or not st.session_state.service_key:
        return None
    try:
        hosp = cached_hospital_list(st.session_state.service_key, (f"{sido} 전체",))
    except api.NEMCError:
        return None
    h = hosp.get(hpid)
    if h and h.lat and h.lon:
        return {"hpid": h.hpid, "name": h.name, "addr": h.addr, "emcls": h.emcls_name, "lat": h.lat, "lon": h.lon, "sido": sido}
    return None


if "origin" not in st.session_state:
    st.session_state.origin = _origin_from_query() or load_config().get("origin") or DEFAULT_ORIGIN
origin = st.session_state.origin


def set_origin(o: dict):
    st.session_state.origin = o
    save_config(origin=o)              # 로컬 실행 시 저장 (클라우드에서는 재시작 시 사라짐 → URL 파라미터 사용)
    try:
        if o.get("hpid"):
            st.query_params["hpid"] = o["hpid"]; st.query_params["sido"] = o["sido"]
    except Exception:
        pass
    st.cache_data.clear()


with st.sidebar:
    st.title("🚑 전원병원 선정")
    st.caption(f"**{origin['name']}** 응급실 → 상급기관 전원 후보 검색")

    if KEY_MANAGED:
        pass  # 운영자가 Secrets로 관리 — 사용자에게 노출하지 않음
    elif st.session_state.service_key:
        k = st.session_state.service_key
        st.success(f"인증키 저장됨 (…{k[-4:]})")
        with st.expander("인증키 변경"):
            new_key = st.text_input("새 인증키 (Decoding)", type="password", key="new_key_input")
            if st.button("저장") and new_key.strip():
                st.session_state.service_key = new_key.strip()
                save_key(new_key.strip()); st.cache_data.clear(); st.rerun()
            if st.button("저장된 키 삭제"):
                st.session_state.service_key = ""
                try: CONFIG_PATH.unlink()
                except Exception: pass
                st.rerun()
    else:
        new_key = st.text_input("공공데이터포털 인증키 (일반 인증키, Decoding)", type="password")
        if st.button("저장하고 시작") and new_key.strip():
            st.session_state.service_key = new_key.strip()
            if save_key(new_key.strip()):
                st.toast("인증키를 config.json에 저장했습니다. 다음부터 자동으로 불러옵니다.")
            st.rerun()
    service_key = st.session_state.service_key

    st.divider()
    st.subheader("기준 병원")
    st.caption(f"현재: {origin['name']} ({origin.get('sido', '')})" + ("" if origin.get("hpid") else " — 기본값, 아래에서 우리 병원으로 바꾸세요"))
    if origin.get("hpid"):
        st.caption("이 병원 기준으로 바로 열리는 주소: 브라우저 주소창의 현재 링크(`?hpid=…`)를 즐겨찾기에 저장하세요.")
    with st.expander("기준 병원 변경", expanded=not origin.get("hpid")):
        o_sido = st.selectbox("시도", options=list(SIDO_NAMES.keys()),
                              index=list(SIDO_NAMES.keys()).index(origin.get("sido")) if origin.get("sido") in SIDO_NAMES else 0)
        o_name = st.text_input("병원명 (일부만 입력해도 됨)", placeholder="예: 동신, 서울적십자")
        if service_key and o_name.strip():
            found, n_total = search_hospital(service_key, o_sido, o_name.strip())
            if not found:
                if n_total == 0:
                    st.warning(f"{o_sido} 기관 목록을 받지 못했습니다. 잠시 후 다시 시도하세요.")
                else:
                    st.warning(f"{o_sido} 응급의료기관 {n_total}곳 중 일치하는 이름이 없습니다. 병원명을 줄여서 입력해 보세요.")
            else:
                pick_o = st.selectbox("검색 결과", options=range(len(found)),
                                      format_func=lambda i: f"{found[i]['name']} — {found[i]['emcls']} — {found[i]['addr'][:22]}")
                if st.button("이 병원을 기준으로 저장", type="primary", use_container_width=True):
                    set_origin(found[pick_o]); st.rerun()
        elif not service_key:
            st.caption("인증키를 먼저 저장하면 병원을 검색할 수 있습니다.")
    with st.expander("좌표 직접 입력 (검색이 안 될 때)", expanded=False):
        m_sido = st.selectbox("시도 ", options=list(SIDO_NAMES.keys()),
                              index=list(SIDO_NAMES.keys()).index(origin.get("sido")) if origin.get("sido") in SIDO_NAMES else 0)
        m_name = st.text_input("표시 이름", value=origin["name"])
        m_lat = st.number_input("위도", value=float(origin["lat"]), format="%.5f")
        m_lon = st.number_input("경도", value=float(origin["lon"]), format="%.5f")
        if st.button("좌표로 저장"):
            set_origin({"hpid": "", "name": m_name.strip() or "기준 병원", "sido": m_sido, "lat": m_lat, "lon": m_lon}); st.rerun()

    st.divider()
    st.subheader("검색 조건")
    _default_regions = [f"{origin.get('sido')} 전체"] if f"{origin.get('sido')} 전체" in REGIONS else ["서울특별시 전체"]
    regions = st.multiselect("검색 지역 (시도 전체 또는 인접 시군구 추가)", options=list(REGIONS.keys()), default=_default_regions)
    max_km = st.slider("최대 거리 (km, 직선)", 3, 500, 15)
    level_sel = st.multiselect("기관 등급", ["권역응급의료센터", "지역응급의료센터", "지역응급의료기관", "응급실운영신고기관"],
                               default=["권역응급의료센터", "지역응급의료센터"])
    min_er = st.number_input("응급실 일반 병상 최소 가용 수", min_value=0, max_value=20, value=1)
    sort_mode = st.radio("정렬", ["수용가능 → 거리", "거리만", "수용가능 → 등급 → 거리"], index=0)
    sort_mode_key = {"수용가능 → 거리": "distance_accept", "거리만": "distance_only",
                     "수용가능 → 등급 → 거리": "level_first"}[sort_mode]
    hide_blocked = st.checkbox("수용 불가/차단 병원 숨기기", value=False)
    c1, c2 = st.columns(2)
    if c1.button("🔄 실시간 갱신", use_container_width=True):
        cached_realtime.clear()
    if c2.button("캐시 초기화", use_container_width=True):
        st.cache_data.clear()

    st.divider()
    st.subheader("전원 기록")
    _store, _store_err = tlog.make_store(APP_DIR, lambda k: st.secrets.get(k) if hasattr(st, "secrets") else None)
    if _store_err:
        st.warning(_store_err)
    try:
        _all_logs = _store.read_all()
    except Exception as _e:
        _all_logs = []; st.caption(f"기록을 읽지 못했습니다: {_e}")
    _n_ep = len({r.get("episode_id") for r in _all_logs})
    st.caption(f"{_store.describe()} · 에피소드 {_n_ep}건 / 연락 {len(_all_logs)}행")
    if _all_logs:
        st.download_button("⬇️ 기록 CSV 다운로드", data=pd.DataFrame(_all_logs).to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"transfer_log_{tlog.now_kst():%Y%m%d}.csv", mime="text/csv", use_container_width=True)

# ---------------------------------------------------------------------------
# 1. 추정 진단 선택
# ---------------------------------------------------------------------------
st.title("응급실 전원병원 선정 도우미")

if "dx_list" not in st.session_state:
    st.session_state.dx_list = []

st.subheader("1️⃣ 추정 진단 선택")
st.caption("진단명이나 약어(STEMI, SAH, 담관염, ectopic, 화상 …)를 입력하면 후보가 좁혀집니다. 환자 정보는 입력하지 않습니다.")
q = st.text_input("진단명 검색", placeholder="예: STEMI, 뇌출혈, 담관염, ectopic, 화상 …", key="dx_query")
hits = tr.search_catalog(q) if q else list(range(len(tr.DIAGNOSIS_CATALOG)))
if q and not hits:
    st.warning("일치하는 진단이 없습니다. 아래 전체 목록에서 골라 주세요.")
    hits = list(range(len(tr.DIAGNOSIS_CATALOG)))

def _cat_label(i: int) -> str:
    name, _a, cat, _r, _n = tr.DIAGNOSIS_CATALOG[i]
    return f"{name}  →  {api.SEVERE_TYPES.get(cat, '분류 외') if cat else '분류 외'}"

sel_idx = st.selectbox("추정 진단 선택", options=hits, format_func=_cat_label, key="dx_catalog_pick")
d1c, d2c = st.columns([1, 3])
if d1c.button("✅ 이 진단으로 전원병원 검색", type="primary", use_container_width=True):
    st.session_state.dx_list = [tr.catalog_dx(sel_idx)]
    st.session_state.dx_pick = 0
    st.rerun()
_n = tr.DIAGNOSIS_CATALOG[sel_idx][4]
if _n:
    d2c.caption("비고: " + _n)

# ---------------------------------------------------------------------------
# 선택 진단 → 분류·필요자원
# ---------------------------------------------------------------------------
dx_list: list[tr.Dx] = st.session_state.dx_list
severe_n: int | None = None
required_res: list[str] = []
chosen_dx: tr.Dx | None = None
if dx_list:
    chosen_dx = dx_list[min(st.session_state.get("dx_pick", 0), len(dx_list) - 1)]
    severe_n = chosen_dx.category or None
    if severe_n == 28:
        severe_n = None  # 응급실 gate keeper는 별도 분류 없이 병상·장비 기준으로만
    required_res = [r for r in chosen_dx.resources if r in RESOURCE_LABELS]

# ---------------------------------------------------------------------------
# 3. 전원병원 후보
# ---------------------------------------------------------------------------
st.subheader("2️⃣ 전원병원 후보")
if chosen_dx is not None:
    sc1, sc2 = st.columns([5, 1])
    _cat_txt = f"{severe_n:02d}. {api.SEVERE_TYPES[severe_n]}" if severe_n else "분류 없음 (응급실 병상·장비 기준)"
    _res_txt = (" · 필요자원: " + ", ".join(RESOURCE_LABELS[r] for r in required_res)) if required_res else ""
    sc1.markdown(f"**선택 진단:** {chosen_dx.name} → **{_cat_txt}**{_res_txt}"
                 + (f"  \n<small>{chosen_dx.note}</small>" if chosen_dx.note else ""), unsafe_allow_html=True)
    if sc2.button("↺ 초기화", use_container_width=True):
        st.session_state.dx_list = []
        st.rerun()
else:
    st.caption("1️⃣에서 진단을 고르면 그에 맞는 수용가능 병원이 조회됩니다. 지금은 응급실 병상·거리 기준입니다.")

if not service_key:
    st.warning("왼쪽 사이드바에서 공공데이터포털 인증키를 저장하세요. (data.go.kr → 마이페이지 → 인증키 발급현황의 **일반 인증키(Decoding)**)")
    st.stop()
if not regions:
    st.warning("검색 지역을 하나 이상 선택하세요.")
    st.stop()

region_t = tuple(regions)
try:
    with st.spinner("응급의료기관 목록 조회 중…"):
        olat, olon, origin_label = float(origin["lat"]), float(origin["lon"]), origin["name"]
        hospitals_raw = cached_hospital_list(service_key, region_t)
    with st.spinner("실시간 병상·중증질환 수용정보 조회 중…"):
        beds, severe, msgs, fetched_at = cached_realtime(service_key, region_t)
except api.NEMCError as e:
    st.error(f"API 오류: {e}")
    st.markdown("- 인증키가 **Decoding 키**인지, 활용신청이 **승인** 상태인지 확인하세요.\n"
                "- 발급 직후에는 반영까지 최대 1시간 정도 걸릴 수 있습니다.")
    st.stop()

hospitals = copy.deepcopy(hospitals_raw)
# 마스터 파일에 기관ID가 없는 기관(NAME:…)은 실시간 데이터의 기관명으로 ID를 찾아 연결
_name_to_hpid = {}
for _src in (beds, severe):
    for _hp, _it in _src.items():
        _nm = (_it.get("dutyName") or "").replace(" ", "")
        if _nm and not _nm.startswith("A"):
            _name_to_hpid.setdefault(_nm, _hp)
for _hp in list(hospitals):
    if _hp.startswith("NAME:"):
        _real = _name_to_hpid.get(_hp[5:].replace(" ", ""))
        if _real and _real not in hospitals:
            hospitals[_real] = hospitals.pop(_hp); hospitals[_real].hpid = _real
merged = api.merge(hospitals, beds, severe, msgs, (olat, olon))

def level_ok(h: api.Hospital) -> bool:
    return not level_sel or any(l in (h.emcls_name or "") for l in level_sel)

origin_hpids = {origin["hpid"]} if origin.get("hpid") else {hp for hp, hh in hospitals.items() if origin["name"] in hh.name}
cands = [h for h in merged if h.hpid not in origin_hpids
         and (h.distance_km is None or h.distance_km <= max_km) and level_ok(h)]

def is_blocked(h: api.Hospital) -> bool:
    if severe_n and api.severe_status(h, severe_n) == "불가":
        return True
    return bool(api.blocked_for(h, severe_n))

if hide_blocked:
    cands = [h for h in cands if not is_blocked(h)]

equip_req = [r for r in required_res if r in api.EQUIP_FIELDS]
bed_req = [r for r in required_res if r not in api.EQUIP_FIELDS]

def bed_missing(h: api.Hospital) -> int:
    return sum(1 for k in bed_req if (h.bed(k) or 0) < 1)

def sort_key(h: api.Hospital) -> tuple:
    k = api.rank_key(h, severe_n, equip_req, int(min_er), sort_mode_key)
    if sort_mode_key == "distance_only":
        return k
    return (k[0], bed_missing(h)) + k[1:]

cands.sort(key=sort_key)

m1, m2, m3, m4 = st.columns(4)
m1.metric("기준 위치", origin_label)
m2.metric("후보 병원 수", len(cands))
if severe_n:
    m3.metric("수용 가능 확인", sum(1 for h in cands if api.severe_status(h, severe_n) == "가능" and not api.blocked_for(h, severe_n)))
else:
    m3.metric("응급실 병상 ≥ 기준", sum(1 for h in cands if (h.bed("hvec") or 0) >= min_er))
m4.metric("실시간 조회 시각", fetched_at.split(" ")[1])
if severe_n:
    st.caption(f"조회 분류: **{api.SEVERE_TYPES[severe_n]}** — 기관별 수용가능 자가입력값과 수용불가 메시지를 함께 반영합니다.")

rows = []
for i, h in enumerate(cands, 1):
    er, er_std = h.bed("hvec"), h.bed("hvs01")
    if severe_n:
        s = api.severe_status(h, severe_n)
        acc = {"가능": "🟢 가능", "불가": "🔴 불가", "정보없음": "⚪ 정보없음"}[s]
        if api.blocked_for(h, severe_n):
            acc = "⛔ 차단메시지"
    else:
        acc = "⛔ 차단메시지" if api.blocked_for(h, None) else "—"
    res_txt = " ".join(
        f"{RESOURCE_LABELS[r]}:{(h.equip(r) or '?') if r in api.EQUIP_FIELDS else (h.bed(r) if h.bed(r) is not None else '?')}"
        for r in required_res)
    rows.append({
        "순위": i, "병원": h.name, "등급": (h.emcls_name or "").replace("응급의료", ""),
        "거리(km)": round(h.distance_km, 1) if h.distance_km is not None else None,
        "수용": acc,
        "응급실 가용/기준": f"{er if er is not None else '?'} / {er_std if er_std is not None else '?'}",
        "필요자원": res_txt,
        "CT/MRI/혈관촬영/Venti": "/".join((h.equip(k) or "?") for k in ("hvctayn", "hvmriayn", "hvangioayn", "hvventiayn")),
        "메시지": " | ".join(api.blocked_for(h, severe_n))[:120],
        "응급실 전화": h.tel_er or h.tel_main,
        "갱신": f"{h.beds_updated[8:10]}:{h.beds_updated[10:12]}" if len(h.beds_updated) >= 12 else "",
        "_lat": h.lat, "_lon": h.lon, "_hpid": h.hpid,
    })
df = pd.DataFrame(rows)
if df.empty:
    st.warning("조건에 맞는 병원이 없습니다. 거리·등급·지역 조건을 넓혀 보세요.")
    st.stop()

tab1, tab2 = st.tabs(["📋 후보 목록", "🗺️ 지도"])
with tab1:
    st.dataframe(df.drop(columns=["_lat", "_lon", "_hpid"]), use_container_width=True, hide_index=True,
                 height=min(60 + 35 * len(df), 700),
                 column_config={"순위": st.column_config.NumberColumn(width="small"),
                                "거리(km)": st.column_config.NumberColumn(format="%.1f", width="small"),
                                "메시지": st.column_config.TextColumn(width="large")})

    st.markdown("**병원 상세**")
    pick_h = st.selectbox("병원 선택", options=[h.hpid for h in cands],
                          format_func=lambda hp: next(f"{r['순위']}. {r['병원']}" for r in rows if r["_hpid"] == hp))
    h = next(x for x in cands if x.hpid == pick_h)
    d1, d2 = st.columns(2)
    with d1:
        st.markdown(f"**{h.name}** — {h.emcls_name}  \n{h.addr}  \n"
                    f"응급실 ☎ **{h.tel_er or '-'}** / 대표 ☎ {h.tel_main or '-'}  \n"
                    f"직선거리 {h.distance_km:.1f} km · 병상정보 입력 {h.beds_updated or '-'}")
        if h.messages:
            st.markdown("**현재 메시지**")
            for m in h.messages:
                st.markdown(f"- [{m.get('symBlkMsgTyp','')}] {m.get('symTypCodMag','')} — {m.get('symBlkMsg','')} "
                            f"({m.get('symBlkSttDtm','')[:12]}~{m.get('symBlkEndDtm','')[:12]})")
        st.markdown("**장비 가용**")
        _equip_label = {"Y": "🟢 가용", "N": "🔴 불가", "N1": "— 미보유"}
        eq_rows = [{"장비": v, "상태": _equip_label.get((h.equip(k) or "").upper(), h.equip(k) or "?")}
                   for k, v in api.EQUIP_FIELDS.items()]
        st.dataframe(pd.DataFrame(eq_rows), hide_index=True, use_container_width=True,
                     height=40 + 35 * len(eq_rows))
    with d2:
        st.markdown("**병상 (가용 / 기준)**")
        bed_rows = [{"구분": label, "가용": h.bed(k), "기준": h.bed(s) if s else None}
                    for k, s, label in api.BED_FIELDS if h.bed(k) is not None or (s and h.bed(s))]
        st.dataframe(pd.DataFrame(bed_rows), hide_index=True, use_container_width=True)
        st.markdown("**중증질환 수용가능 (자가입력)**")
        sev_rows = [{"질환": api.SEVERE_TYPES[n], "상태": api.severe_status(h, n), "비고": h.severe_msg.get(n, "")}
                    for n in api.SEVERE_TYPES if n in h.severe]
        if sev_rows:
            st.dataframe(pd.DataFrame(sev_rows), hide_index=True, use_container_width=True, height=300)
        else:
            st.caption("중증질환 수용정보 없음")
    if st.button("기관 기본정보(진료과목·진료시간) 불러오기"):
        info = cached_basic(service_key, h.hpid)
        if info:
            st.dataframe(pd.DataFrame([
                {"항목": "진료과목", "내용": info.get("dgidIdName", "")},
                {"항목": "총병상", "내용": info.get("hpbdn", "")},
                {"항목": "응급실 병상", "내용": info.get("hperyn", "")},
                {"항목": "수술실", "내용": info.get("hpopyn", "")},
                {"항목": "간이약도", "내용": info.get("dutyMapimg", "")},
                {"항목": "기관설명", "내용": info.get("dutyInf", "")},
            ]), hide_index=True, use_container_width=True)

with tab2:
    m = df.dropna(subset=["_lat", "_lon"]).copy()
    def _color(v: str):
        if v.startswith("🟢"): return "#2e7d32"
        if v.startswith("🔴") or v.startswith("⛔"): return "#c62828"
        return "#1565c0"
    m["color"] = m["수용"].map(_color); m["size"] = 120
    origin_df = pd.DataFrame([{"_lat": olat, "_lon": olon, "color": "#ff9800", "size": 200}])
    st.map(pd.concat([m[["_lat", "_lon", "color", "size"]], origin_df]), latitude="_lat", longitude="_lon",
           color="color", size="size", zoom=11)
    st.caption("주황: 동신병원 · 초록: 수용가능 · 빨강: 불가/차단 · 파랑: 정보없음")

# ---------------------------------------------------------------------------
# 3. 전원 기록 (연구용 로그 — 환자 식별정보 없음)
# ---------------------------------------------------------------------------
st.subheader("3️⃣ 전원 기록")
if "ep" not in st.session_state:
    st.session_state.ep = {"id": tlog.new_episode_id(), "calls": []}
ep = st.session_state.ep
_row_by_hpid = {r["_hpid"]: r for r in rows}
_top5 = "|".join(f"{r['병원']}({r['수용']},{r['거리(km)']}km)" for r in rows[:5])

with st.expander("연락한 병원 추가", expanded=True):
    e1, e2, e3, e4 = st.columns([3, 1.2, 1.5, 1.2])
    call_hp = e1.selectbox("연락한 병원", options=[h.hpid for h in cands],
                           format_func=lambda hp: f"{_row_by_hpid[hp]['순위']}. {_row_by_hpid[hp]['병원']}", key="call_hp")
    call_result = e2.selectbox("결과", ["수용", "거부", "무응답·보류"], key="call_result")
    refusal = e3.selectbox("거부 사유", ["", "병상 없음", "중환자실 없음", "전문의 부재", "수술·시술 중", "장비 불가", "환자 상태 부적합", "기타"],
                           key="refusal")
    call_time = e4.text_input("연락 시각 (HH:MM, 비우면 추가 시각)", value="", key="call_time")
    _sel_rank = _row_by_hpid[call_hp]["순위"] if call_hp in _row_by_hpid else None
    d1, d2 = st.columns([1.2, 3])
    habitual = d1.checkbox("평소 전원하던 기관", value=False, key="habitual")
    if _sel_rank is not None and _sel_rank != 1 and len(ep["calls"]) == 0:
        deviation = d2.selectbox("앱 1순위가 아닌 기관에 먼저 연락한 이유",
                                 ["", "정보 불신(과거 거부 경험 등)", "기관 간 관계·전원 절차", "배후 진료과 사정", "기타"],
                                 key="deviation")
    else:
        deviation = ""
    if st.button("➕ 목록에 추가", use_container_width=True):
        r = _row_by_hpid[call_hp]
        ep["calls"].append({
            "call_order": len(ep["calls"]) + 1, "hospital_hpid": call_hp, "hospital_name": r["병원"], "hospital_level": r["등급"],
            "app_rank": r["순위"], "app_accept_status": r["수용"], "app_er_beds": r["응급실 가용/기준"],
            "app_distance_km": r["거리(km)"],
            "_call_time_exact": call_time.strip() or tlog.now_hhmm_exact(),   # 소요시간 계산용, 저장 전 제거
            "call_time": tlog.floor_hhmm(call_time.strip() or tlog.now_hhmm_exact()),
            "call_logged_at": tlog.now_str(), "call_result": call_result,
            "refusal_reason": refusal if call_result != "수용" else "",
            "habitual": "Y" if habitual else "N", "deviation_reason": deviation,
        })
        st.rerun()

if ep["calls"]:
    st.dataframe(pd.DataFrame(ep["calls"])[["call_order", "hospital_name", "app_rank", "app_accept_status", "habitual", "call_time", "call_result", "refusal_reason"]]
                 .rename(columns={"call_order": "순서", "hospital_name": "병원", "app_rank": "앱 순위", "app_accept_status": "앱 표시",
                                  "habitual": "관행", "call_time": "연락", "call_result": "결과", "refusal_reason": "사유"}),
                 hide_index=True, use_container_width=True)
    if st.button("마지막 항목 삭제"):
        ep["calls"].pop(); st.rerun()

with st.form("episode_form"):
    f1, f2, f3, f4 = st.columns(4)
    age_band = f1.selectbox("연령대", ["", "0-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90+"])
    sex_ = f2.selectbox("성별", ["", "M", "F"])
    ktas = f3.selectbox("KTAS", ["", "1", "2", "3", "4", "5"])
    outcome = f4.selectbox("에피소드 결과", ["전원 완료", "전원 못함 — 자체 처치/입원", "전원 못함 — 사망", "전원 못함 — 기타", "기록만"])
    g1, g2, g3 = st.columns([1, 1, 3])
    decision_time = g1.text_input("전원 결정 시각 (HH:MM)", value="")
    accept_time = g2.text_input("수용 확정 시각 (HH:MM)", value="")
    note = g3.text_input("메모 (식별정보 금지)", value="")
    _acc_opts = [""] + [c["hospital_hpid"] for c in ep["calls"] if c["call_result"] == "수용"]
    accepted_hp = st.selectbox("최종 전원 병원", options=_acc_opts, index=len(_acc_opts) - 1,
                               format_func=lambda hp: "(없음)" if not hp else next(c["hospital_name"] for c in ep["calls"] if c["hospital_hpid"] == hp))
    saved = st.form_submit_button("💾 에피소드 저장", type="primary", use_container_width=True)
if saved:
    if not ep["calls"] and outcome == "전원 완료":
        st.error("연락한 병원을 먼저 추가하세요.")
    elif chosen_dx is None and not st.session_state.get("allow_no_dx"):
        st.warning("추정 진단이 선택되지 않았습니다. 1️⃣에서 진단을 고른 뒤 저장하세요. "
                   "진단 없이 저장하려면 아래를 체크하고 다시 저장을 누르세요.")
        st.checkbox("진단 없이 저장", key="allow_no_dx")
    else:
        base = {"episode_id": ep["id"], "logged_at": tlog.now_str(), "origin_hpid": origin.get("hpid", ""), "origin_name": origin["name"],
                "age_band": age_band, "sex": sex_, "ktas": ktas,
                "diagnosis": chosen_dx.name if chosen_dx else "", "category_no": severe_n or "",
                "category_name": api.SEVERE_TYPES.get(severe_n, "") if severe_n else "", "app_top5": _top5,
                "episode_outcome": outcome,
                "decision_time": tlog.floor_hhmm(decision_time), "accept_time": tlog.floor_hhmm(accept_time),
                "decision_to_accept_min": tlog.minutes_between(decision_time, accept_time), "note": note}
        out_rows = []
        for c in ep["calls"] or [{}]:
            r = dict(base); r.update(c); r["final_accepted"] = "Y" if c.get("hospital_hpid") and c.get("hospital_hpid") == accepted_hp else "N"
            r["call_offset_min"] = tlog.minutes_between(decision_time, r.pop("_call_time_exact", ""))
            out_rows.append(r)
        try:
            _store.append(out_rows)
            st.success(f"저장했습니다 ({len(out_rows)}행, {_store.describe()}).")
            st.session_state.ep = {"id": tlog.new_episode_id(), "calls": []}
            st.session_state.dx_list = []
            st.session_state.pop("allow_no_dx", None)
            st.rerun()
        except Exception as e:
            st.error(f"저장 실패: {e}")

st.divider()
st.caption("병상·수용가능 정보는 각 기관 자가입력값으로, "
           "전원 결정 전 반드시 응급실 직통전화로 확인하십시오. 거리는 직선거리입니다. "
           "자료: 국립중앙의료원 전국 응급의료기관 정보 조회 서비스(공공데이터포털).")
