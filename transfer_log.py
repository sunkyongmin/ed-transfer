"""
전원 기록 저장 (연구용, 환자 식별정보 없음).

저장소
  - 기본: 앱 폴더의 transfer_log.csv (로컬 실행용)
  - Google Sheets: Streamlit secrets에 [gsheet] 설정이 있으면 시트에 append (클라우드용, 재시작해도 유지)
      [gsheet]
      url = "https://docs.google.com/spreadsheets/d/…"
      service_account = '{ … 서비스계정 JSON 전체 … }'
    requirements.txt 에 gspread 필요.

레코드 = 전원 시도 1건(episode) 당 여러 행(연락한 병원마다 1행). 열 정의는 FIELDS 참고.
"""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)
from pathlib import Path

FIELDS = [
    "episode_id",        # 전원 에피소드 ID (같은 환자의 연락 시도들을 묶음)
    "logged_at",         # 기록 시각
    "origin_hpid", "origin_name",
    "age_band", "sex", "ktas",           # 선택 입력 (식별 불가 수준)
    "diagnosis", "category_no", "category_name",
    "app_top5",          # 앱이 보여준 상위 5개 후보 "병원명(수용상태,거리km)|…"
    "call_order",        # 이 병원이 몇 번째 연락인지
    "hospital_hpid", "hospital_name", "hospital_level",
    "app_rank", "app_accept_status", "app_er_beds", "app_distance_km",
    "call_time",         # 연락 시각 (사용자 입력, HH:MM)
    "call_logged_at",    # '목록에 추가'를 누른 실제 시각 (자동)
    "call_result",       # 수용 / 거부 / 무응답·보류
    "refusal_reason",    # 거부 사유
    "final_accepted",    # 이 병원으로 최종 전원 여부 (Y/N)
    "episode_outcome",   # 전원 완료 / 전원 못함(자체 처치) / 전원 못함(사망) / 기타
    "decision_time",     # 전원 결정 시각 (HH:MM)
    "accept_time",       # 수용 확정 시각 (HH:MM)
    "note",
]


class CsvStore:
    def __init__(self, path: Path):
        self.path = path

    def _migrate_if_needed(self):
        """기존 파일의 열이 FIELDS와 다르면(열 추가 등) 새 구조로 다시 씀."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            if rd.fieldnames == FIELDS:
                return
            old_rows = list(rd)
        with self.path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in old_rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    def append(self, rows: list[dict]):
        self._migrate_if_needed()
        new = not self.path.exists()
        with self.path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def describe(self) -> str:
        return f"CSV: {self.path.name}"


class GSheetStore:
    def __init__(self, url: str, service_account_json: str):
        import gspread  # type: ignore
        creds = json.loads(service_account_json)
        gc = gspread.service_account_from_dict(creds)
        self.ws = gc.open_by_url(url).sheet1
        if not self.ws.row_values(1):
            self.ws.append_row(FIELDS)

    def append(self, rows: list[dict]):
        self.ws.append_rows([[r.get(k, "") for k in FIELDS] for r in rows])

    def read_all(self) -> list[dict]:
        return self.ws.get_all_records()

    def describe(self) -> str:
        return "Google Sheets"


def make_store(app_dir: Path, secrets_get) -> tuple[object, str | None]:
    """(store, 오류메시지). secrets에 gsheet가 있으면 시트, 아니면 CSV."""
    try:
        gs = secrets_get("gsheet")
    except Exception:
        gs = None
    if gs:
        try:
            return GSheetStore(gs["url"], gs["service_account"]), None
        except Exception as e:  # 설정 오류 시 CSV로 폴백하되 메시지 남김
            return CsvStore(app_dir / "transfer_log.csv"), f"Google Sheets 연결 실패({e}); CSV로 저장합니다."
    return CsvStore(app_dir / "transfer_log.csv"), None


def new_episode_id() -> str:
    return now_kst().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def now_str() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")
