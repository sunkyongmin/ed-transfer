# 클라우드 배포 (Streamlit Community Cloud, 무료)

환자 정보를 입력하지 않는 버전이므로 외부 서버에 올려도 개인정보 문제가 없습니다.
결과: `https://<원하는이름>.streamlit.app` 주소로 어디서나 접속, 맥북을 켜둘 필요 없음.

## 준비물
- GitHub 계정 (github.com, 무료)
- 공공데이터포털 인증키 (Decoding)

## 0. 마스터 파일 생성 (최초 1회, 1분)
```bash
cd ~/bigdata/ed-transfer
source .venv/bin/activate
python build_master.py
```
`hospitals_master.csv`가 생기고 시도별 건수가 출력됩니다.

## 1. GitHub 저장소 만들기 (최초 1회, 10분)
1. github.com 로그인 → 우상단 **+** → **New repository**
2. Repository name: `ed-transfer` / **Public** 선택 / **Create repository**
3. 터미널에서 (ed-transfer 폴더에서):
   ```bash
   cd ~/bigdata/ed-transfer
   git init
   git add app.py nemc_api.py triage_rules.py build_master.py hospitals_master.csv requirements.txt README.md DEPLOY.md .gitignore .streamlit/config.toml .streamlit/secrets.toml.example test_nemc_api.py test_triage_rules.py
   git commit -m "ED transfer hospital finder"
   git branch -M main
   git remote add origin https://github.com/<GitHub아이디>/ed-transfer.git
   git push -u origin main
   ```
   (`config.json`, `secrets.toml`, `.venv`는 `.gitignore`로 제외되어 올라가지 않습니다. 올라갔는지 GitHub 페이지에서 꼭 확인하세요.)

## 2. Streamlit Community Cloud 배포 (5분)
1. https://share.streamlit.io 접속 → **Sign in with GitHub**
2. **Create app** (또는 New app) → "Deploy a public app from GitHub"
3. Repository: `<아이디>/ed-transfer`, Branch: `main`, Main file path: `app.py`
4. App URL: 원하는 이름 (예: `ed-transfer-kr`)
5. **Advanced settings** → **Secrets** 칸에 붙여넣기:
   ```toml
   NEMC_API_KEY = "여기에_Decoding_인증키"
   APP_PASSWORD = "원하는_비밀번호"
   ```
6. **Deploy** → 1~2분 뒤 앱이 뜹니다.

## 3. 각 병원별 링크
앱에서 사이드바 → 기준 병원 변경 → 병원 검색 → 저장하면 주소창이
`https://<이름>.streamlit.app/?hpid=A1100014&sido=서울특별시` 처럼 바뀝니다.
이 링크를 그 병원 응급실 PC 즐겨찾기에 넣어두면 매번 기준 병원을 고를 필요가 없습니다.

## 4. 이후 수정 반영
파일을 고친 뒤:
```bash
cd ~/bigdata/ed-transfer
git add -A
git commit -m "update"
git push
```
푸시하면 1분 안에 클라우드 앱이 자동으로 다시 배포됩니다.

## 주의
- 무료 플랜은 앱을 며칠 안 쓰면 절전 상태가 되어 첫 접속에 30초쯤 걸릴 수 있습니다(화면의 버튼을 누르면 깨어남).
- 인증키 하나를 여러 병원이 공유하므로 공공데이터포털 일일 트래픽 한도(개발계정 1,000건/일)를 넘길 수 있습니다.
  이용자가 늘면 공공데이터포털에서 **운영계정 전환**(트래픽 증량)을 신청하세요.
- 비밀번호는 Secrets에서 언제든 바꿀 수 있습니다(저장 후 앱 Reboot).

## 5. 전원 기록을 클라우드에서 유지하기 (Google Sheets)

클라우드 서버는 재시작되면 파일이 사라지므로, 전원 기록은 Google 스프레드시트에 쌓도록 설정합니다. (로컬 실행은 설정 없이 `transfer_log.csv`에 저장)

1. Google 스프레드시트를 새로 만들고 URL을 복사합니다.
2. https://console.cloud.google.com → 프로젝트 생성 → "API 및 서비스" → **Google Sheets API** 사용 설정.
3. "사용자 인증 정보" → **서비스 계정** 만들기 → 키 추가 → JSON 다운로드.
4. 스프레드시트 **공유**에 서비스 계정 이메일(`…@….iam.gserviceaccount.com`)을 **편집자**로 추가.
5. Streamlit Cloud 앱 설정 → Secrets 에 추가:
   ```toml
   [gsheet]
   url = "https://docs.google.com/spreadsheets/d/…/edit"
   service_account = '''
   { … 다운로드한 JSON 파일 내용 전체 … }
   '''
   ```
6. 앱 Reboot. 사이드바 "전원 기록"에 `Google Sheets`라고 표시되면 연결된 것입니다.
