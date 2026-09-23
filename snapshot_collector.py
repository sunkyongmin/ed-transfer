"""
응급의료 종합상황판(국립중앙의료원 OpenAPI) 데이터 품질 분석용 스냅샷 수집기.

일정 간격으로 지정 지역의 (1) 실시간 가용병상, (2) 중증질환 수용가능, (3) 수용불가 메시지를 조회해
CSV로 누적 저장합니다. 환자 정보 없음. 앱과 독립적으로 동작.

사용법 (ed-transfer 폴더, 가상환경 켠 상태):
    python snapshot_collector.py                       # 전국. 서울·경기 30분, 그 외 시도 120분 간격 (Ctrl+C 종료)
    python snapshot_collector.py --slow-interval 30    # 운영계정 승인 후: 전국 30분
    python snapshot_collector.py --regions 서울특별시   # 특정 시도만
    python snapshot_collector.py --once                # 1회만 수집(모든 지정 시도)
수집 시각은 정시·30분 경계에 맞춤. 느린 시도는 그 간격의 배수가 되는 경계(예: 00, 02, 04시)에만 수집.

산출 파일 (snapshots/ 폴더):
    beds_YYYYMM.csv      기관별 병상·장비 필드 전체 + 입력시각(hvidate) + 수집시각
    severe_YYYYMM.csv    기관별 27개 분류 수용가능 값 + 수집시각
    messages_YYYYMM.csv  수용불가 메시지 (기관, 분류, 사유, 시작/종료) + 수집시각
    collector.log        수집 로그
API 호출: 지역 1곳당 회차마다 3건 (기본 설정 전국 = 하루 약 790건, 개발계정 1,000건 한도 이내)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import nemc_api as api

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "snapshots"
KST = ZoneInfo("Asia/Seoul")

SIDO_ALIASES = {
    "서울특별시": ["서울특별시"], "경기도": ["경기도"], "인천광역시": ["인천광역시"], "부산광역시": ["부산광역시"],
    "대구광역시": ["대구광역시"], "대전광역시": ["대전광역시"], "울산광역시": ["울산광역시"], "세종특별자치시": ["세종특별자치시", "세종시"],
    "전남광주통합특별시": ["전남광주통합특별시", "광주광역시", "전라남도"], "강원특별자치도": ["강원특별자치도", "강원도"],
    "충청북도": ["충청북도"], "충청남도": ["충청남도"], "전북특별자치도": ["전북특별자치도", "전라북도"],
    "경상북도": ["경상북도"], "경상남도": ["경상남도"], "제주특별자치도": ["제주특별자치도", "제주도"],
}


def load_key() -> str:
    try:
        k = json.loads((HERE / "config.json").read_text(encoding="utf-8")).get("NEMC_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("NEMC_API_KEY", "")


def log(msg: str):
    line = f"{datetime.now(KST):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with (OUT_DIR / "collector.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_rows(path: Path, rows: list[dict], fixed_first: list[str]):
    """열이 회차마다 달라질 수 있어(API 응답 필드 가변) 파일 헤더는 첫 생성 시 고정하고, 새 열은 무시하지 않도록 합집합으로 재작성."""
    if not rows:
        return
    cols_new = list(dict.fromkeys(fixed_first + [k for r in rows for k in r.keys()]))
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            old_cols = rd.fieldnames or []
            if set(cols_new) - set(old_cols):
                old_rows = list(rd)
                cols = list(dict.fromkeys(old_cols + cols_new))
                with path.open("w", encoding="utf-8-sig", newline="") as fw:
                    w = csv.DictWriter(fw, fieldnames=cols); w.writeheader()
                    for r in old_rows:
                        w.writerow({c: r.get(c, "") for c in cols})
            else:
                cols = old_cols
    else:
        cols = cols_new
        with path.open("w", encoding="utf-8-sig", newline="") as fw:
            csv.DictWriter(fw, fieldnames=cols).writeheader()
    with path.open("a", encoding="utf-8-sig", newline="") as fa:
        w = csv.DictWriter(fa, fieldnames=cols)
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def fetch_region(key: str, sido: str, fn):
    last = None
    for nm in SIDO_ALIASES.get(sido, [sido]):
        try:
            got = fn(key, nm)
            if got:
                return got
        except api.NEMCError as e:
            last = e
    if last:
        raise last
    return {}


FAST_SIDO = ["서울특별시", "경기도"]
ALL_SIDO = list(SIDO_ALIASES.keys())


def sidos_due(regions: list[str], fast_min: int, slow_min: int, now: datetime) -> list[str]:
    """이번 30분 경계에 수집할 시도. 자정 기준 경과 분이 해당 간격의 배수인 시도만."""
    mod = now.hour * 60 + (now.minute // 30) * 30
    return [s for s in regions if mod % (fast_min if s in FAST_SIDO else slow_min) == 0]


def sleep_to_next_boundary(now: datetime):
    nxt = ((now.minute // 30) + 1) * 30
    wait = (nxt - now.minute) * 60 - now.second
    time.sleep(max(wait, 1))


def collect_once(key: str, regions: list[str]):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    ym = datetime.now(KST).strftime("%Y%m")
    for sido in regions:
        try:
            beds = fetch_region(key, sido, api.fetch_realtime_beds)
            rows = [{"collected_at": ts, "sido": sido, **v} for v in beds.values()]
            append_rows(OUT_DIR / f"beds_{ym}.csv", rows, ["collected_at", "sido", "hpid", "dutyName", "hvidate"])
            n_beds = len(rows)
        except api.NEMCError as e:
            n_beds = -1; log(f"[{sido}] 병상 조회 실패: {e}")
        try:
            sev = fetch_region(key, sido, api.fetch_severe_acceptance)
            rows = [{"collected_at": ts, "sido": sido, **v} for v in sev.values()]
            append_rows(OUT_DIR / f"severe_{ym}.csv", rows, ["collected_at", "sido", "hpid", "dutyName"])
            n_sev = len(rows)
        except api.NEMCError as e:
            n_sev = -1; log(f"[{sido}] 수용가능 조회 실패: {e}")
        try:
            msgs = fetch_region(key, sido, api.fetch_messages)
            rows = [{"collected_at": ts, "sido": sido, **m} for lst in msgs.values() for m in lst]
            append_rows(OUT_DIR / f"messages_{ym}.csv", rows,
                        ["collected_at", "sido", "hpid", "dutyName", "symBlkMsgTyp", "symTypCod", "symTypCodMag",
                         "symBlkMsg", "symBlkSttDtm", "symBlkEndDtm"])
            n_msg = len(rows)
        except api.NEMCError as e:
            n_msg = -1; log(f"[{sido}] 메시지 조회 실패: {e}")
        log(f"[{sido}] 병상 {n_beds}기관, 수용가능 {n_sev}기관, 메시지 {n_msg}건")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="서울·경기 수집 간격(분), 기본 30")
    ap.add_argument("--slow-interval", type=int, default=120, help="그 외 시도 수집 간격(분), 기본 120. 30의 배수")
    ap.add_argument("--regions", nargs="+", default=ALL_SIDO, help="시도명 (기본 전국)")
    ap.add_argument("--once", action="store_true", help="1회만 수집")
    a = ap.parse_args()
    if a.interval % 30 or a.slow_interval % 30:
        sys.exit("간격은 30의 배수(분)로 지정하세요.")
    key = load_key()
    if not key:
        sys.exit("인증키를 찾지 못했습니다 (config.json 또는 NEMC_API_KEY).")
    OUT_DIR.mkdir(exist_ok=True)
    log(f"수집 시작: {len(a.regions)}개 시도, 서울·경기 {a.interval}분 / 그 외 {a.slow_interval}분, 출력 {OUT_DIR}")
    if a.once:
        collect_once(key, a.regions)
        return
    while True:
        now = datetime.now(KST)
        due = sidos_due(a.regions, a.interval, a.slow_interval, now)
        if due:
            try:
                collect_once(key, due)
            except Exception as e:  # 네트워크 등 예기치 못한 오류에도 계속
                log(f"오류: {e}")
        sleep_to_next_boundary(datetime.now(KST))


if __name__ == "__main__":
    main()
