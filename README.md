# 동신병원 응급실 전원병원 선정 도우미

국립중앙의료원 **전국 응급의료기관 정보 조회 서비스**(공공데이터포털 OpenAPI, `ErmctInfoInqireService`)의 실시간 데이터를 이용해,
동신병원 응급실에서 전원이 필요한 환자에게 맞는 상급기관 후보를 거리·수용가능 여부·병상·장비 기준으로 정렬해 보여주는 Streamlit 앱입니다.

## 1. 설치 (macOS, 최초 1회)

```bash
cd ed-transfer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 인증키 설정

공공데이터포털(data.go.kr) → 마이페이지 → **인증키 발급현황**에서 **일반 인증키(Decoding)** 값을 복사합니다.
(Encoding 키를 넣으면 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가 납니다.)

둘 중 하나로 저장하면 앱 실행 시 자동으로 읽습니다.

```bash
# 방법 A: secrets 파일
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
#   → 파일을 열어 NEMC_API_KEY 값 입력

# 방법 B: 환경변수
export NEMC_API_KEY="발급받은_Decoding_키"
```

저장하지 않아도 앱 왼쪽 사이드바에서 직접 입력할 수 있습니다.
활용신청 승인 직후에는 키 반영까지 최대 1시간 정도 걸릴 수 있습니다.

## 3. 실행

```bash
source .venv/bin/activate
streamlit run app.py
```

브라우저가 `http://localhost:8501` 로 열립니다. 응급실 다른 PC에서 접속하려면
`streamlit run app.py --server.address 0.0.0.0` 로 실행하고 `http://<맥의 IP>:8501` 로 접속합니다.

## 4. 사용법

사이드바에서 환자 상태와 검색 조건을 고르면 본문에 후보 병원 표가 나옵니다.

| 항목 | 설명 |
|---|---|
| 중증질환 분류 | 국립중앙의료원 중증응급질환 27개 분류. 선택하면 각 기관이 입력한 **수용가능(Y/N)** 값과 **수용불가 메시지**를 반영해 정렬 |
| 필요 장비 | CT·MRI·혈관촬영기·인공호흡기·CRRT·ECMO 등 — 가용 `Y`인 기관을 우선 |
| 필요 병상 | 중환자실(내과·외과·신경·심장·흉부·신생아 등)·수술실 — 가용 1 이상인 기관을 우선 |
| 응급실 최소 가용 | 응급실 일반 병상 가용 수가 이 값 미만이면 후순위 |
| 검색 지역 | 서울 전체 + 인접 경기(고양·부천·광명·과천·안양·성남·구리·하남·김포 등) 기본 선택 |
| 최대 거리 | 동신병원 기준 직선거리(km) |
| 기관 등급 | 권역/지역응급의료센터 기본 선택. 지역응급의료기관도 포함하려면 추가 |
| 정렬 | 수용가능→거리(기본) / 거리만 / 수용가능→등급→거리 |

정렬 규칙(기본): ① 선택 질환 수용가능 확인(🟢) → 정보없음(⚪) → 불가/차단메시지(🔴⛔) ② 필요 병상·장비 충족 ③ 응급실 병상 기준 충족 ④ 거리순.

표 아래 **병원 상세**에서 응급실 직통전화, 전체 병상 현황(가용/기준), 중증질환 수용가능 전체 목록, 현재 떠 있는 메시지를 볼 수 있고,
지도 탭에서 위치를 확인할 수 있습니다. 실시간 정보는 60초 캐시되며 사이드바의 **실시간 갱신** 버튼으로 즉시 새로 고칩니다.

## 5. 사용 API 오퍼레이션

| 오퍼레이션 | 용도 | 캐시 |
|---|---|---|
| `getEgytListInfoInqire` | 기관 목록·등급·좌표·전화 | 6시간 |
| `getEmrrmRltmUsefulSckbdInfoInqire` | 응급실 실시간 가용병상·장비 | 60초 |
| `getSrsillDissAceptncPosblInfoInqire` | 중증질환자 수용가능 여부 | 60초 |
| `getEmrrmSrsillDissMsgInqire` | 응급실·중증질환 수용불가 메시지 | 60초 |
| `getEgytBassInfoInqire` | 기관 기본정보(진료과목 등, 버튼 클릭 시) | 24시간 |

일반 인증키의 일일 트래픽 한도(개발계정 기준 보통 1,000건/일)를 고려해 지역 단위로 묶어 호출하고 캐시를 둡니다.
한 번 조회에 지역 수 × 3~4건 정도 소모됩니다.

## 6. 파일 구성

```
app.py               Streamlit UI
nemc_api.py          API 호출·XML 파싱·병합·정렬 로직 (UI와 독립, 다른 프로그램에서 재사용 가능)
test_nemc_api.py     활용가이드 샘플 응답 기반 단위 테스트 (python test_nemc_api.py)
smoke_test_app.py    streamlit 없이 app.py 런타임 점검 (개발용)
requirements.txt
.streamlit/secrets.toml.example
```

## 7. 주의

- 병상·수용가능 정보는 각 기관이 직접 입력하는 값으로 지연·누락이 있을 수 있습니다. **전원 결정 전 반드시 응급실 직통전화로 확인**하십시오.
- 거리는 직선거리이며 실제 이송시간과 다릅니다.
- 동신병원 좌표는 API에서 자동 조회하며, 실패 시 `app.py` 상단 `ORIGIN_FALLBACK` 값을 사용합니다. 위치가 어긋나면 그 값을 수정하세요.
- 경기 지역은 시군구 이름(예: "고양시")으로 먼저 조회하고, 결과가 없으면 구 단위("고양시 덕양구" 등)로 재시도합니다.
