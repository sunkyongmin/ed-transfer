"""
전국 응급의료기관 마스터 목록(hospitals_master.csv) 생성.

사용법
  1) API로 생성 (권장):   python build_master.py
       - config.json / 환경변수 NEMC_API_KEY / .streamlit/secrets.toml 의 인증키 사용
       - 17개 시도를 차례로 조회하고 시도별 건수를 출력
  2) 공공데이터포털에서 내려받은 "전국 응급의료기관 현황" CSV/XLSX를 합치기:
       python build_master.py --from-file 다운로드파일.csv
       - 기관ID(hpid)가 없는 파일이면 이름으로 실시간 데이터와 매칭되도록 hpid는 비워둠
  3) 둘 다: API 결과에 파일 결과를 보충 (같은 hpid/이름은 API 우선)
       python build_master.py --from-file 파일.csv

결과: hospitals_master.csv (hpid,name,emcls,emcls_name,addr,tel_main,tel_er,lat,lon,sido)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import nemc_api as api

HERE = Path(__file__).resolve().parent
OUT = HERE / "hospitals_master.csv"

SIDO_NAMES = {
    "서울특별시": ["서울특별시"], "부산광역시": ["부산광역시"], "대구광역시": ["대구광역시"], "인천광역시": ["인천광역시"],
    "전남광주통합특별시": ["전남광주통합특별시", "광주광역시", "전라남도"],
    "대전광역시": ["대전광역시"], "울산광역시": ["울산광역시"],
    "세종특별자치시": ["세종특별자치시", "세종시"], "경기도": ["경기도"],
    "강원특별자치도": ["강원특별자치도", "강원도"], "충청북도": ["충청북도"], "충청남도": ["충청남도"],
    "전북특별자치도": ["전북특별자치도", "전라북도"], "경상북도": ["경상북도"], "경상남도": ["경상남도"],
    "제주특별자치도": ["제주특별자치도", "제주도"],
}
_ALIAS_TO_SIDO = {a: s for s, al in SIDO_NAMES.items() for a in al}
_ALIAS_TO_SIDO.update({"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "전남광주통합특별시",
                       "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
                       "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "전남": "전남광주통합특별시", "경북": "경상북도",
                       "경남": "경상남도", "제주": "제주특별자치도"})

FIELDS = ["hpid", "name", "emcls", "emcls_name", "addr", "tel_main", "tel_er", "lat", "lon", "sido"]


def sido_from_addr(addr: str) -> str:
    tok = (addr or "").split()[0] if addr else ""
    for a, s in _ALIAS_TO_SIDO.items():
        if tok.startswith(a):
            return s
    return ""


def load_key() -> str:
    try:
        k = json.loads((HERE / "config.json").read_text(encoding="utf-8")).get("NEMC_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    k = os.environ.get("NEMC_API_KEY", "")
    if k:
        return k
    p = HERE / ".streamlit" / "secrets.toml"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("NEMC_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def from_api(key: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for sido, names in SIDO_NAMES.items():
        got = {}
        for nm in names:
            try:
                got = api.fetch_hospital_list(key, nm)
            except api.NEMCError as e:
                print(f"  [{sido}] '{nm}' 조회 실패: {e}")
                continue
            if got:
                break
        print(f"  {sido:10s} {len(got):4d}건")
        for hpid, h in got.items():
            rows[hpid] = {"hpid": hpid, "name": h.name, "emcls": h.emcls, "emcls_name": h.emcls_name, "addr": h.addr,
                          "tel_main": h.tel_main, "tel_er": h.tel_er, "lat": h.lat or "", "lon": h.lon or "",
                          "sido": sido_from_addr(h.addr) or sido}
    return rows


def _pick(row: dict, *keys):
    for k in row:
        kl = k.replace(" ", "").lower()
        for want in keys:
            if want in kl:
                return (row[k] or "").strip()
    return ""


def from_file(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        try:
            import pandas as pd
        except ImportError:
            sys.exit("xlsx를 읽으려면 pandas/openpyxl이 필요합니다: pip install pandas openpyxl")
        df = pd.read_excel(p, dtype=str).fillna("")
        records = df.to_dict("records")
    else:
        raw = p.read_bytes()
        text = None
        for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
            try:
                text = raw.decode(enc); break
            except UnicodeDecodeError:
                continue
        if text is None:
            sys.exit("CSV 인코딩을 읽을 수 없습니다.")
        records = list(csv.DictReader(text.splitlines()))
    out = []
    for r in records:
        name = _pick(r, "기관명", "병원명", "dutyname", "명칭")
        if not name:
            continue
        addr = _pick(r, "주소", "소재지", "dutyaddr")
        lat = _pick(r, "위도", "lat"); lon = _pick(r, "경도", "lon")
        try:
            lat = float(lat); lon = float(lon)
        except ValueError:
            lat = lon = ""
        emcls_name = _pick(r, "종별", "분류명", "기관구분", "emclsname")
        out.append({"hpid": _pick(r, "기관id", "기관코드", "hpid", "암호화"), "name": name, "emcls": _pick(r, "emcls"),
                    "emcls_name": emcls_name, "addr": addr, "tel_main": _pick(r, "대표전화", "전화번호", "tel1"),
                    "tel_er": _pick(r, "응급실전화", "응급실", "tel3"), "lat": lat, "lon": lon,
                    "sido": sido_from_addr(addr) or _pick(r, "시도")})
    print(f"  파일에서 {len(out)}건 읽음 ({p.name})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", help="공공데이터포털 전국 응급의료기관 현황 CSV/XLSX")
    ap.add_argument("--no-api", action="store_true", help="API 조회 생략")
    a = ap.parse_args()

    rows: dict[str, dict] = {}
    if not a.no_api:
        key = load_key()
        if not key:
            print("인증키를 찾지 못했습니다 (config.json / NEMC_API_KEY / secrets.toml). --no-api 로 파일만 사용할 수 있습니다.")
        else:
            print("API로 시도별 응급의료기관 목록 조회 중…")
            rows = from_api(key)
    if a.from_file:
        by_name = {(r["name"].replace(" ", ""), r["sido"]) for r in rows.values()}
        added = 0
        for r in from_file(a.from_file):
            k = (r["name"].replace(" ", ""), r["sido"])
            if r["hpid"] and r["hpid"] in rows:
                continue
            if k in by_name:
                continue
            rows[r["hpid"] or f"NAME:{r['name']}"] = r; added += 1
        print(f"  파일에서 {added}건 보충")

    if not rows:
        sys.exit("생성할 데이터가 없습니다.")
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["sido"], r["name"])):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    by_sido: dict[str, int] = {}
    for r in rows.values():
        by_sido[r["sido"]] = by_sido.get(r["sido"], 0) + 1
    print(f"\n완료: {OUT} ({len(rows)}건)")
    for s_, n in sorted(by_sido.items()):
        print(f"  {s_ or '(시도 미상)':10s} {n}")


if __name__ == "__main__":
    main()
