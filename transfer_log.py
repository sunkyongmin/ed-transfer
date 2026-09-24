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

# ---------------------------------------------------------------------------
# 시각 저장 규칙 (IRB 연구계획서 v3.3, 4.3·5.2)
#   - 절대 시각은 30분 구간으로 내림하여 저장한다 (예: 14:47 → 14:30).
#   - 소요시간(분)은 내림 전 정확한 입력값으로 계산하여 별도 열에 저장한다.
#   - episode_id 에는 날짜만 넣고 시:분:초는 넣지 않는다.
# ---------------------------------------------------------------------------
TIME_BUCKET_MIN = 30


def floor_dt(dt: datetime, bucket: int = TIME_BUCKET_MIN) -> datetime:
    return dt.replace(minute=(dt.minute // bucket) * bucket, second=0, microsecond=0)


def floor_hhmm(s: str, bucket: int = TIME_BUCKET_MIN) -> str:
    """'HH:MM' 문자열을 30분 구간으로 내림. 형식이 아니면 빈 문자열."""
    s = (s or "").strip()
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if not (0 <= h < 24 and 0 <= m < 60):
            return ""
        return f"{h:02d}:{(m // bucket) * bucket:02d}"
    except Exception:
        return ""


def minutes_between(start_hhmm: str, end_hhmm: str) -> str:
    """두 'HH:MM' 사이의 분(자정 넘김은 +24h). 둘 중 하나라도 없으면 ''."""
    try:
        sh, sm = map(int, start_hhmm.strip().split(":"))
        eh, em = map(int, end_hhmm.strip().split(":"))
    except Exception:
        return ""
    d = (eh * 60 + em) - (sh * 60 + sm)
    if d < -720:
        d += 24 * 60
    return str(d)

FIELDS = [
    "episode_id",        # 전원 에피소드 ID (같은 환자의 연락 시도들을 묶음)
    "logged_at",         # 기록 시각 (30분 구간으로 내림)
    "origin_hpid", "origin_name",
    "age_band", "sex", "ktas",           # 선택 입력 (식별 불가 수준)
    "diagnosis", "category_no", "category_name",
    "app_top5",          # 앱이 보여준 상위 5개 후보 "병원명(수용상태,거리km)|…"
    "call_order",        # 이 병원이 몇 번째 연락인지
    "hospital_hpid", "hospital_name", "hospital_level",
    "app_rank", "app_accept_status", "app_er_beds", "app_distance_km",
    "call_time",         # 연락 시각 (사용자 입력 HH:MM → 30분 구간으로 내림 저장)
    "call_logged_at",    # '목록에 추가'를 누른 시각 (자동, 30분 구간으로 내림)
    "call_offset_min",   # 전원 결정 시각부터 이 연락까지 분 (정확한 입력값으로 계산)
    "call_result",       # 수용 / 거부 / 무응답·보류
    "refusal_reason",    # 거부 사유
    "habitual",          # 평소 전원하던 기관 여부 (Y/N)
    "deviation_reason",  # 앱 1순위가 아닌 기관에 먼저 연락한 사유 (첫 연락에만 기록)
    "final_accepted",    # 이 병원으로 최종 전원 여부 (Y/N)
    "episode_outcome",   # 전원 완료 / 전원 못함(자체 처치) / 전원 못함(사망) / 기타
    "decision_time",     # 전원 결정 시각 (HH:MM → 30분 구간으로 내림 저장)
    "accept_time",       # 수용 확정 시각 (HH:MM → 30분 구간으로 내림 저장)
    "decision_to_accept_min",  # 결정→수용 확정 소요 분 (정확한 입력값으로 계산)
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
        header = self.ws.row_values(1)
        if not header:
            self.ws.append_row(FIELDS)
            header = list(FIELDS)
        missing = [f for f in FIELDS if f not in header]
        if missing:
            # 기존 열 순서는 그대로 두고 새 열만 뒤에 붙임 (기존 기록 열 밀림 방지)
            header = header + missing
            self.ws.update(values=[header], range_name="1:1")
        self._cols = header

    def append(self, rows: list[dict]):
        self.ws.append_rows([[r.get(k, "") for k in self._cols] for r in rows])

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
    """날짜 + 난수. 시:분:초는 넣지 않는다."""
    return now_kst().strftime("%Y%m%d-") + uuid.uuid4().hex[:8]


def now_str() -> str:
    """현재 시각을 30분 구간으로 내림한 'YYYY-MM-DD HH:MM'."""
    return floor_dt(now_kst()).strftime("%Y-%m-%d %H:%M")


def now_hhmm_exact() -> str:
    """내림 전 현재 'HH:MM' (소요시간 계산용, 저장하지 않음)."""
    return now_kst().strftime("%H:%M")
