"""
국립중앙의료원 전국 응급의료기관 정보 조회 서비스(ErmctInfoInqireService) 클라이언트.

공공데이터포털 OpenAPI 활용가이드 V13(2026.08.27) 기준.
사용 오퍼레이션:
  - getEgytListInfoInqire            응급의료기관 목록 (등급·주소·좌표)
  - getEmrrmRltmUsefulSckbdInfoInqire 응급실 실시간 가용병상
  - getSrsillDissAceptncPosblInfoInqire 중증질환자 수용가능 정보
  - getEmrrmSrsillDissMsgInqire      응급실·중증질환 수용불가 메시지
  - getEgytBassInfoInqire            기관 기본정보 (진료과목·연락처 등)
"""
from __future__ import annotations

import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"
TIMEOUT = 12


# ---------------------------------------------------------------------------
# 코드표
# ---------------------------------------------------------------------------

# 중증질환 수용가능 정보 (getSrsillDissAceptncPosblInfoInqire) mkiosktyN 필드
SEVERE_TYPES: dict[int, str] = {
    1: "[재관류중재술] 심근경색",
    2: "[재관류중재술] 뇌경색",
    3: "[뇌출혈수술] 거미막하출혈",
    4: "[뇌출혈수술] 거미막하출혈 외",
    5: "[대동맥응급] 흉부",
    6: "[대동맥응급] 복부",
    7: "[담낭담관질환] 담낭질환",
    8: "[담낭담관질환] 담도포함질환",
    9: "[복부응급수술] 비외상",
    10: "[장중첩/폐색] 영유아",
    11: "[응급내시경] 성인 위장관",
    12: "[응급내시경] 영유아 위장관",
    13: "[응급내시경] 성인 기관지",
    14: "[응급내시경] 영유아 기관지",
    15: "[저체중출생아] 집중치료",
    16: "[산부인과응급] 분만",
    17: "[산부인과응급] 산과수술",
    18: "[산부인과응급] 부인과수술",
    19: "[중증화상] 전문치료",
    20: "[사지접합] 수족지접합",
    21: "[사지접합] 수족지접합 외",
    22: "[응급투석] HD",
    23: "[응급투석] CRRT",
    24: "[정신과적응급] 폐쇄병동입원",
    25: "[안과적응급] 응급",
    26: "[영상의학혈관중재] 성인",
    27: "[영상의학혈관중재] 영유아",
    28: "응급실 (Emergency gate keeper)",
}

# 메시지 조회 symTypCod → 위 번호
SYMTYP_TO_SEVERE: dict[str, int] = {
    "Y0010": 1, "Y0020": 2, "Y0031": 3, "Y0032": 4, "Y0041": 5, "Y0042": 6,
    "Y0051": 7, "Y0052": 8, "Y0060": 9, "Y0070": 10, "Y0081": 11, "Y0082": 12,
    "Y0091": 13, "Y0092": 14, "Y0100": 15, "Y0111": 16, "Y0112": 17, "Y0113": 18,
    "Y0120": 19, "Y0131": 20, "Y0132": 21, "Y0141": 22, "Y0142": 23, "Y0150": 24,
    "Y0160": 25, "Y0171": 26, "Y0172": 27, "Y000": 28,
}

# 실시간 가용병상 필드 (가용 필드, 기준 필드, 표시명)
BED_FIELDS: list[tuple[str, str | None, str]] = [
    ("hvec", "hvs01", "응급실 일반"),
    ("hv28", "hvs02", "응급실 소아"),
    ("hv29", "hvs03", "응급실 음압격리"),
    ("hv30", "hvs04", "응급실 일반격리"),
    ("hvoc", "hvs22", "수술실"),
    ("hvicc", "hvs17", "중환자실 일반"),
    ("hv2", "hvs06", "중환자실 내과"),
    ("hv3", "hvs07", "중환자실 외과"),
    ("hvcc", "hvs11", "중환자실 신경과"),
    ("hv6", "hvs12", "중환자실 신경외과"),
    ("hvccc", "hvs16", "중환자실 흉부외과"),
    ("hv34", "hvs15", "중환자실 심장내과"),
    ("hvncc", "hvs08", "중환자실 신생아"),
    ("hv32", "hvs09", "중환자실 소아"),
    ("hv8", "hvs13", "중환자실 화상"),
    ("hv9", "hvs14", "중환자실 외상"),
    ("hv35", "hvs18", "중환자실 음압격리"),
    ("hv31", "hvs05", "응급전용 중환자실"),
    ("hvgc", "hvs38", "입원실 일반"),
    ("hv36", "hvs19", "응급전용 입원실"),
    ("hv40", "hvs24", "정신과 폐쇄병동"),
    ("hv42", "hvs26", "분만실"),
    ("hv41", "hvs25", "입원실 음압격리"),
]

# 장비 가용여부 필드 (Y/N)
EQUIP_FIELDS: dict[str, str] = {
    "hvctayn": "CT",
    "hvmriayn": "MRI",
    "hvangioayn": "혈관촬영기",
    "hvventiayn": "인공호흡기",
    "hvventisoayn": "인공호흡기(조산아)",
    "hvincuayn": "인큐베이터",
    "hvcrrtayn": "CRRT",
    "hvecmoayn": "ECMO",
    "hvoxyayn": "고압산소치료기",
    "hvhypoayn": "중심체온조절유도기",
    "hvamyn": "구급차",
}

# 응급의료기관 등급 코드 → 정렬 우선순위 (낮을수록 상위)
EMCLS_RANK: dict[str, int] = {
    "G001": 0,  # 권역응급의료센터
    "G002": 1,  # 전문응급의료센터(구)
    "G003": 1,
    "G004": 1,
    "G005": 1,
    "G006": 2,  # 지역응급의료센터
    "G007": 3,  # 지역응급의료기관
    "G099": 4,  # 응급실운영신고기관
}


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

@dataclass
class Hospital:
    hpid: str
    name: str
    emcls: str = ""
    emcls_name: str = ""
    addr: str = ""
    tel_main: str = ""
    tel_er: str = ""
    lat: float | None = None
    lon: float | None = None
    distance_km: float | None = None

    # 실시간 병상 (getEmrrmRltmUsefulSckbdInfoInqire)
    beds: dict[str, Any] = field(default_factory=dict)
    beds_updated: str = ""

    # 중증질환 수용가능 (getSrsillDissAceptncPosblInfoInqire): {n: "Y"/"N"/"불가"/"정보미제공"/...}
    severe: dict[int, str] = field(default_factory=dict)
    severe_msg: dict[int, str] = field(default_factory=dict)

    # 수용불가 메시지 (getEmrrmSrsillDissMsgInqire)
    messages: list[dict[str, str]] = field(default_factory=list)

    def bed(self, key: str) -> int | None:
        v = self.beds.get(key)
        if v in (None, "", "NULL"):
            return None
        try:
            return int(float(v))
        except ValueError:
            return None

    def equip(self, key: str) -> str | None:
        v = self.beds.get(key)
        if v in (None, "", "NULL"):
            return None
        return v.strip()


class NEMCError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 저수준 호출
# ---------------------------------------------------------------------------

def _text(el: ET.Element | None, tag: str, default: str = "") -> str:
    if el is None:
        return default
    c = el.find(tag)
    if c is None or c.text is None:
        return default
    return c.text.strip()


def parse_response(xml_text: str) -> tuple[list[dict[str, str]], int]:
    """XML → (item dict 목록, totalCount). 게이트웨이/서비스 오류는 NEMCError."""
    # 일부 응답(메시지 조회)은 standalone="true" 등 비표준 XML 선언을 보내므로 선언부를 제거하고 파싱
    xml_text = xml_text.lstrip("﻿ \r\n\t")
    if xml_text.startswith("<?xml"):
        end = xml_text.find("?>")
        if end != -1:
            xml_text = xml_text[end + 2:]
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise NEMCError(f"XML 파싱 실패: {e}; 응답 앞부분: {xml_text[:200]!r}")

    # 공공데이터포털 게이트웨이 오류 형식
    if root.tag == "OpenAPI_ServiceResponse":
        hdr = root.find("cmmMsgHeader")
        raise NEMCError(
            f"게이트웨이 오류 {_text(hdr, 'returnReasonCode')}: {_text(hdr, 'returnAuthMsg') or _text(hdr, 'errMsg')}"
        )

    header = root.find("header")
    code = _text(header, "resultCode", "??")
    if code not in ("00", "0"):
        raise NEMCError(f"서비스 오류 {code}: {_text(header, 'resultMsg')}")

    body = root.find("body")
    items: list[dict[str, str]] = []
    if body is not None:
        for it in body.iter("item"):
            items.append({c.tag: (c.text or "").strip() for c in it})
    total = 0
    try:
        total = int(_text(body, "totalCount", "0") or 0)
    except ValueError:
        pass
    return items, total


def call(service_key: str, op: str, params: dict[str, Any], *, retries: int = 2) -> list[dict[str, str]]:
    """페이지를 끝까지 따라가며 모든 item을 반환."""
    if not service_key:
        raise NEMCError("인증키(serviceKey)가 없습니다.")
    rows = 200
    page = 1
    out: list[dict[str, str]] = []
    while True:
        q = {"serviceKey": service_key, "pageNo": page, "numOfRows": rows}
        q.update({k: v for k, v in params.items() if v not in (None, "")})
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = requests.get(f"{BASE_URL}/{op}", params=q, timeout=TIMEOUT)
                r.raise_for_status()
                items, total = parse_response(r.text)
                last_err = None
                break
            except (requests.RequestException, NEMCError) as e:
                last_err = e
                if isinstance(e, NEMCError) and ("게이트웨이" in str(e) or "서비스 오류" in str(e)):
                    raise  # 인증키 오류 등은 재시도 무의미
                time.sleep(0.5 * (attempt + 1))
        if last_err:
            raise NEMCError(f"{op} 호출 실패: {last_err}")
        out.extend(items)
        if len(out) >= total or not items:
            break
        page += 1
    return out


# ---------------------------------------------------------------------------
# 고수준 조회
# ---------------------------------------------------------------------------

def _f(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_hospital_list(service_key: str, sido: str, sigungu: str | None = None) -> dict[str, Hospital]:
    """응급의료기관 목록 (등급·좌표). key=hpid"""
    items = call(service_key, "getEgytListInfoInqire", {"Q0": sido, "Q1": sigungu, "ORD": "NAME"})
    res: dict[str, Hospital] = {}
    for it in items:
        hpid = it.get("hpid", "")
        if not hpid:
            continue
        res[hpid] = Hospital(
            hpid=hpid,
            name=it.get("dutyName", ""),
            emcls=it.get("dutyEmcls", ""),
            emcls_name=it.get("dutyEmclsName", ""),
            addr=it.get("dutyAddr", ""),
            tel_main=it.get("dutyTel1", ""),
            tel_er=it.get("dutyTel3", ""),
            lat=_f(it.get("wgs84Lat", "")),
            lon=_f(it.get("wgs84Lon", "")),
        )
    return res


def fetch_realtime_beds(service_key: str, sido: str, sigungu: str | None = None) -> dict[str, dict[str, str]]:
    """실시간 가용병상. key=hpid → 원본 필드 dict"""
    items = call(service_key, "getEmrrmRltmUsefulSckbdInfoInqire", {"STAGE1": sido, "STAGE2": sigungu})
    return {it["hpid"]: it for it in items if it.get("hpid")}


def fetch_severe_acceptance(service_key: str, sido: str, sigungu: str | None = None,
                            sm_type: int | None = None) -> dict[str, dict[str, str]]:
    """중증질환자 수용가능 정보. key=hpid → 원본 필드 dict (mkiosktyN, MKioskTyNMsg …)"""
    items = call(service_key, "getSrsillDissAceptncPosblInfoInqire",
                 {"STAGE1": sido, "STAGE2": sigungu, "SM_TYPE": sm_type})
    out: dict[str, dict[str, str]] = {}
    for it in items:
        hpid = it.get("hpid") or ""
        # 가이드 샘플처럼 dutyName에 hpid가 들어오는 경우가 있어 보정
        if not hpid and it.get("dutyName", "").startswith("A"):
            hpid = it["dutyName"]
        if hpid:
            out[hpid] = it
    return out


def fetch_messages(service_key: str, sido: str, sigungu: str | None = None) -> dict[str, list[dict[str, str]]]:
    """응급실·중증질환 수용불가 메시지. key=hpid → 메시지 목록"""
    items = call(service_key, "getEmrrmSrsillDissMsgInqire", {"Q0": sido, "Q1": sigungu})
    out: dict[str, list[dict[str, str]]] = {}
    for it in items:
        hpid = it.get("hpid") or it.get("emcOrgCod") or ""
        if hpid:
            out.setdefault(hpid, []).append(it)
    return out


def fetch_basic_info(service_key: str, hpid: str) -> dict[str, str]:
    items = call(service_key, "getEgytBassInfoInqire", {"HPID": hpid})
    return items[0] if items else {}


def find_hospital_by_name(service_key: str, name: str, sido: str) -> Hospital | None:
    items = call(service_key, "getEgytListInfoInqire", {"Q0": sido, "QN": name})
    for it in items:
        if name in it.get("dutyName", ""):
            return Hospital(
                hpid=it.get("hpid", ""), name=it.get("dutyName", ""),
                emcls=it.get("dutyEmcls", ""), emcls_name=it.get("dutyEmclsName", ""),
                addr=it.get("dutyAddr", ""), tel_main=it.get("dutyTel1", ""), tel_er=it.get("dutyTel3", ""),
                lat=_f(it.get("wgs84Lat", "")), lon=_f(it.get("wgs84Lon", "")),
            )
    return None


# ---------------------------------------------------------------------------
# 병합·정렬
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _severe_value(raw: dict[str, str], n: int) -> str:
    """mkiosktyN / MKioskTyN 대소문자 혼재 대응."""
    for k in (f"mkioskty{n}", f"MKioskTy{n}", f"MKIOSKTY{n}"):
        if k in raw:
            return raw[k].strip()
    return ""


def _severe_msg(raw: dict[str, str], n: int) -> str:
    for k in (f"MKioskTy{n}Msg", f"mkioskty{n}msg", f"MKIOSKTY{n}MSG"):
        if k in raw:
            return raw[k].strip()
    return ""


def merge(hospitals: dict[str, Hospital],
          beds: dict[str, dict[str, str]],
          severe: dict[str, dict[str, str]],
          messages: dict[str, list[dict[str, str]]],
          origin: tuple[float, float] | None) -> list[Hospital]:
    for hpid, h in hospitals.items():
        if hpid in beds:
            h.beds = beds[hpid]
            h.beds_updated = beds[hpid].get("hvidate", "")
        if hpid in severe:
            raw = severe[hpid]
            for n in SEVERE_TYPES:
                v = _severe_value(raw, n)
                if v:
                    h.severe[n] = v
                m = _severe_msg(raw, n)
                if m:
                    h.severe_msg[n] = m
        h.messages = messages.get(hpid, [])
        if origin and h.lat is not None and h.lon is not None:
            h.distance_km = haversine_km(origin[0], origin[1], h.lat, h.lon)
    return list(hospitals.values())


def severe_status(h: Hospital, n: int) -> str:
    """'가능' / '불가' / '정보없음'"""
    v = h.severe.get(n, "")
    if v.upper() == "Y" or v == "가능":
        return "가능"
    if v.upper() == "N" or "불가" in v:
        return "불가"
    return "정보없음"


def blocked_for(h: Hospital, n: int | None) -> list[str]:
    """선택 중증질환(n) 또는 응급실 전체에 대해 현재 떠 있는 수용불가 메시지."""
    out = []
    for m in h.messages:
        code = m.get("symTypCod", "")
        target = SYMTYP_TO_SEVERE.get(code)
        if target == 28 or (n is not None and target == n) or (n is None and target is not None):
            typ = m.get("symBlkMsgTyp", "")
            msg = m.get("symBlkMsg", "")
            label = m.get("symTypCodMag", "") or SEVERE_TYPES.get(target or 0, code)
            out.append(f"[{typ}] {label}: {msg}".strip())
    return out


def rank_key(h: Hospital, n: int | None, required_equip: list[str], min_er_beds: int,
             mode: str = "distance_accept") -> tuple:
    """정렬 키. 낮을수록 상위."""
    # 0: 수용가능 확인, 1: 정보없음, 2: 불가 또는 차단메시지
    if n is None:
        acc = 0
    else:
        s = severe_status(h, n)
        acc = {"가능": 0, "정보없음": 1, "불가": 2}[s]
    if blocked_for(h, n):
        acc = max(acc, 2)
    # 장비
    equip_missing = sum(1 for k in required_equip if (h.equip(k) or "N").upper().startswith("N"))
    # 응급실 병상
    er = h.bed("hvec")
    er_pen = 0 if er is None else (0 if er >= min_er_beds else 1)
    dist = h.distance_km if h.distance_km is not None else 9999.0
    if mode == "distance_only":
        return (dist,)
    if mode == "level_first":
        return (acc, equip_missing, EMCLS_RANK.get(h.emcls, 5), er_pen, dist)
    return (acc, equip_missing, er_pen, dist)
