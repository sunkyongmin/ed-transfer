# analysis/ — 공개 데이터 품질 분석 스크립트

2026 한국보건행정학회 후기학술대회 초록·포스터의 수치를 재현한다. 입력은 `snapshots/` 의 월별 CSV(수집기 `snapshot_collector.py` 산출물)와 `hospitals_master.csv`(종별), 출력은 `analysis/out/`.

| 스크립트 | 하는 일 | 출력 |
|---|---|---|
| `quality.py` | 완전성·최신성·일관성·과밀/여유·수용불가 메시지·시도별 비교 (초록 결과란) | `히트맵_미입력.csv`, `시도별.csv`, `고유메시지.csv`, `beds_slim.pkl` |
| `bootstrap_strata.py` | 기관 군집 부트스트랩 95% CI, 평일·휴일 × 주간·야간 층화, 센터급 핵심 분류 민감도 | `표_군집부트스트랩.csv`, `표_층화*.csv`, `표_핵심분류_미입력.csv`, `과밀_시간대.csv` |
| `figures.py` | 포스터 그림 4장 (300dpi PNG) | `그림1~4_*.png` |
| `common.py` | 공통 로더·신뢰구간·인자 | |

## 의존성

```
pip install pandas numpy matplotlib
```

## 실행

리포지토리 루트에서, 가상환경을 켠 뒤:

```
python analysis/quality.py --tag nat
python analysis/bootstrap_strata.py --tag nat
python analysis/figures.py --tag nat --n-label "44,139,233" --subtitle "전국 416개 응급의료기관, 2026-09-24~10-12, 30분 간격 876회차"
```

서울만, 9/22부터:

```
python analysis/quality.py --sido 서울특별시 --start "2026-09-22 14:50" --end "2026-10-06" --tag seoul
```

공통 인자: `--snapshots`(CSV 폴더, 기본 `snapshots/`), `--start`, `--end`(미포함), `--sido`(기본 all), `--out`, `--tag`(출력 파일명 꼬리표).

## 지표 정의 (요약)

- 완전성: 27개 중증질환 분류의 수용가능 값이 "정보미제공"인 비율. 분석 단위 기관-시점-분류.
- 최신성: 병상 정보 입력시각(hvidate) 대비 수집시각 시차(분). 평가 기준집의 자동 15분·수동 60분 초과 비율. 수용가능 정보는 갱신시각이 없어 기간 중 값 변경 여부를 대리지표로 사용.
- 일관성: 수용가능 "Y"와 같은 질환의 수용불가 메시지가 함께 게시된 기관-시점 비율 (메시지 코드 → 분류 대응은 `nemc_api.SYMTYP_TO_SEVERE`).
- 과밀: 응급실 일반 가용병상(hvec) < 0 인 시점 (재실 환자 > 기준병상 hvs01). 여유: 가용 ≥ 기준의 절반.
- 메시지: 기관·유형·질환코드·문구·게시 시작시각이 같으면 1건. 사유는 문구 키워드 규칙(`quality.reason`)으로 1차 분류.
- 신뢰구간: 비율에 Wilson; 같은 기관의 반복 관측을 고려한 보고용 구간은 기관 복원추출 군집 부트스트랩(기본 2,000회, seed 20261006).

## 주의

- 응급실운영신고기관은 종별 기능 규정 밖이라 제외한다(`common.CL`).
- 전국 19일 자료는 메모리 2 GB 안팎을 쓴다.
- `out/` 은 커밋하지 않는다 (`.gitignore` 참고).
