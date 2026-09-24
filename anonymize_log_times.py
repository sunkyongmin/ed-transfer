"""
기존 transfer_log.csv 의 시각을 30분 구간으로 내림하고 소요시간 열을 채우는 1회성 변환 스크립트.
실행: python anonymize_log_times.py            (transfer_log.csv 를 제자리에서 변환, 원본은 transfer_log.csv.bak 로 보존)
Google Sheets 에 기록 중이면 시트를 CSV로 내려받아 같은 방법으로 변환한 뒤 다시 올리세요.
"""
import csv
import shutil
from datetime import datetime
from pathlib import Path

import transfer_log as tlog

SRC = Path(__file__).parent / "transfer_log.csv"


def floor_datetime_str(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return tlog.floor_dt(datetime.strptime(s, fmt)).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return s


def main():
    if not SRC.exists():
        print("transfer_log.csv 가 없습니다."); return
    bak = SRC.with_suffix(".csv.bak")
    shutil.copy(SRC, bak)
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        dec, acc, call = r.get("decision_time", ""), r.get("accept_time", ""), r.get("call_time", "")
        if not r.get("decision_to_accept_min"):
            r["decision_to_accept_min"] = tlog.minutes_between(dec, acc)
        if not r.get("call_offset_min"):
            r["call_offset_min"] = tlog.minutes_between(dec, call)
        r["decision_time"], r["accept_time"], r["call_time"] = tlog.floor_hhmm(dec), tlog.floor_hhmm(acc), tlog.floor_hhmm(call)
        r["logged_at"] = floor_datetime_str(r.get("logged_at", ""))
        r["call_logged_at"] = floor_datetime_str(r.get("call_logged_at", ""))
        eid = r.get("episode_id", "")
        if len(eid) >= 15 and eid[8] == "-" and eid[9:15].isdigit():   # 구형 YYYYMMDD-HHMMSS-xxxx
            r["episode_id"] = eid[:9] + __import__("uuid").uuid4().hex[:8]
    with SRC.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=tlog.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in tlog.FIELDS})
    print(f"{len(rows)}행 변환 완료. 원본은 {bak.name} 에 보존했습니다(확인 후 삭제하세요).")


if __name__ == "__main__":
    main()
