# QR 피싱 분석기 (팀 장장 · AI:ON 2026)

QR코드에 담긴 링크가 **피싱 사이트인지** 판정하는 웹 서비스입니다.
규칙 기반 점수(신호 등급·안전 신호 감점·조합 규칙)에 **AI 페이지 사칭 판별**을 신호 하나로 더해 판정합니다.

- 상세 요구사항: [docs/requirements.md](docs/requirements.md)
- 검증 결과(발표용 표): [eval/results.md](eval/results.md)

## 1. 설치 (처음 한 번)

Windows PowerShell 기준입니다. Python 3.12가 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # 그다음 .env 파일을 열어 API 키 두 개를 채워 넣기
.\.venv\Scripts\python scripts\update_whitelist.py   # 신뢰 사이트 목록(Tranco 상위 1만 개) 만들기
```

| 환경변수 (.env) | 어디서 발급 | 없으면 |
|---|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) | AI 사칭 판별이 "AI 분석 불가"로 표시되고 규칙만으로 판정 |
| `SAFE_BROWSING_API_KEY` | Google Cloud 콘솔 → Safe Browsing API | DB 조회가 "확인 불가"로 표시 |

`.env`는 Git에 올라가지 않습니다. 키를 코드나 문서에 직접 적지 마세요.

## 2. 실행

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```
브라우저에서 <http://localhost:8000> 접속 → 첫 화면에서 바로 카메라가 켜집니다. (모바일 세로 화면 기준)
사진 올리기·링크 직접 입력은 카메라 아래 **"다른 방법으로 검사"** 메뉴에 있습니다.

- **카메라**는 보안 정책상 `localhost` 또는 `https` 주소에서만 켜집니다. 권한이 없으면 "카메라로 QR 스캔" 버튼이 대신 보입니다.
- **휴대폰으로 시연하기** (휴대폰에서 `http://PC주소:8000`으로 접속하면 카메라가 안 켜짐 → https 터널 사용)
  1. [ngrok](https://ngrok.com/download) 설치·가입 후 인증 토큰 등록: `ngrok config add-authtoken <토큰>`
  2. 서버를 켠 상태에서 다른 터미널에서 `ngrok http 8000`
  3. 화면에 나온 `https://xxxx.ngrok-free.app` 주소를 휴대폰 브라우저로 열기
  - 시연에는 실제 피싱 주소 대신 가짜 주소·시연용 QR을 씁니다.
- 시연용 QR 이미지: `python scripts/make_demo_qr.py` → `demo/` 폴더 (정상 1개, Google 공식 테스트 피싱 주소 1개, 두 개가 함께 있는 이미지)

## 3. 검증 데이터 수집과 성능 측정

```powershell
.\.venv\Scripts\python scripts\collect.py --phish 100 --benign 100   # 저장본 수집 (여러 번 실행하면 누적)
.\.venv\Scripts\python scripts\collect.py --fix-domains              # 도메인 등록일 조회 실패분만 다시 채우기
.\.venv\Scripts\python scripts\evaluate.py                           # 성능 측정 → eval/results.md
.\.venv\Scripts\python scripts\evaluate.py --no-ai                   # AI 빼고 빠르게
```

- 피싱 URL은 [OpenPhish 무료 피드](https://openphish.com/feed.txt), 정상 URL은 Tranco 상위 목록과 `config/benign_extra.txt`에서 가져옵니다.
- 피싱 사이트는 금방 사라지므로 수집할 때 HTML·리다이렉트·도메인·인증서·Safe Browsing 결과를 모두 저장합니다(`data/snapshots/`, Git 제외).
- 검증은 저장본으로만 돌리므로 다시 실행해도 같은 결과가 나옵니다. AI 응답도 `data/ai_cache/`에 저장해 재사용합니다.

## 4. 테스트

```powershell
.\.venv\Scripts\python -m pytest
```

## 5. 판정 방식

| 등급 | 예시 | 반영 |
|---|---|---|
| 확정 신호 | Safe Browsing·OpenPhish 피싱 DB 등재 | 점수와 관계없이 **위험** |
| 강한 신호 | 브랜드 유사 도메인, 30일 미만 신규 도메인, IP 주소 URL, 단축 URL이 다른 도메인으로 이동, 브랜드와 도메인이 다른 로그인 폼, **AI 사칭 판별** | 높은 배점 |
| 약한 신호 | http, 긴 URL, 서브도메인·파라미터·인코딩 과다, 악용 잦은 TLD, 무료 호스팅 주소 | 낮은 배점, **합계 상한**. 약한 신호만으로는 최대 **주의** |
| 안전 신호 | 신뢰 사이트 목록, 오래된 도메인, 정상 인증서 | 감점 (확정 신호는 상쇄 못 함) |
| 조합 규칙 | http + 비밀번호 입력, 신규 도메인 + 브랜드 유사, 단축 URL + 신규 도메인 | 추가 가중 |

- **판정 위계** (`config/rules_hierarchy.json`): 범죄유형(지금은 피싱) > 큐싱 예방 수칙(대분류) > 위 점수 신호(소분류).
  점수는 위 표대로 계산하고, 결과 화면은 가장 영향이 큰 근거가 속한 **예방 수칙 한 줄 + 행동 안내**를 먼저 보여 준 뒤, "자세히 보기"에서 수칙별로 묶은 근거를 보여 줍니다. 수칙 문구·행동 안내는 이 파일에서 고칩니다. (2단계에서 범죄유형 추가로 확장)
- **피싱 판정과 기타 위협은 따로 표시합니다.** 기타 위협·주의사항(악성코드 DB, 실행 파일 다운로드, http, 인증서 오류)은 피싱 판정을 바꾸지 않습니다. 단, 악성코드 DB 등재는 종합 판정을 위험으로 올리되 "악성코드 위험"으로 안내하고 피싱 판정은 따로 보여 줍니다.
- **AI 사칭 판별**: 서버가 페이지 HTML에서 제목·본문·입력란·이미지·링크 도메인을 뽑아 Gemini에 보내고, AI는 "어떤 서비스를 표방하는지 / 페이지 목적 / 요구 정보 / 공식 도메인 추정"만 답합니다. 실제 도메인과의 비교와 점수 반영은 규칙이 합니다. 피싱 DB 등재나 신뢰 사이트처럼 규칙으로 판정이 분명하면 AI를 호출하지 않습니다.
  일관성을 위해 고정 질문·고정 JSON 형식·temperature 0·seed 고정을 쓰고, 실시간 분석에서는 같은 URL의 결과를 24시간(`settings.json`의 `ai.url_cache_hours`) 재사용합니다.
- 배점·임계값·조합 규칙: `config/scoring.json` / 접속 제한·AI 모델·단축 URL·호스팅 목록: `config/settings.json` / 신뢰 목록 추가: `config/whitelist_custom.txt`
- 브랜드 목록: 팀 조사 목록 `config/brands_team.csv`(101개)를 고친 뒤 `python scripts/import_brands.py` 를 실행하면 `config/brands.json` 이 다시 만들어집니다. (`brands.json` 은 직접 고치지 않기)

## 6. 안전 규칙 (분석 대상 접속)

- 접속은 서버에서만, GET 요청만 합니다. 폼 제출·스크립트 실행 등 상호작용은 하지 않습니다.
- 리다이렉트 횟수·접속 시간·HTML 크기를 제한하고, 실행 파일 등은 헤더만 보고 내려받지 않습니다.
- 내부망 주소(127.0.0.1, 192.168.x.x 등)로는 접속하지 않습니다.

## 7. 폴더 구조

```
app/            서버 코드
  main.py         웹 서버(API, 화면 제공)
  snapshot.py     분석에 필요한 정보 수집 (실시간·검증 공통 형식)
  fetcher.py      안전한 페이지 접속 (리다이렉트 추적, 크기·시간 제한)
  lookups.py      도메인 등록일(RDAP), 인증서, Safe Browsing, OpenPhish
  page.py         HTML에서 제목·입력란·링크 등 추출
  brand.py        브랜드 유사 도메인, 페이지 속 브랜드 찾기
  ai_judge.py     Gemini 사칭 판별
  context.py      QR 안내문 맥락 비교 (2단계 자리)
  scoring.py      점수 계산·판정
  hierarchy.py    판정 위계: 근거를 예방 수칙별로 묶고 대표 수칙·행동 안내 선택
  analyzer.py     위 모듈을 묶는 분석 파이프라인
  qr_decode.py    이미지 속 QR 읽기
static/         화면 (HTML/CSS/JS)
config/         배점·임계값·브랜드·신뢰 목록 등 설정
scripts/        수집·검증·목록 갱신·시연 QR 생성 스크립트
tests/          단위 테스트
eval/           검증 결과
docs/           요구사항
```

## 8. 2단계 계획

- QR 안내문 맥락 일치 분석: 카메라 프레임/업로드 이미지에서 안내 목적을 AI로 뽑아 실제 페이지 목적과 비교 (`app/context.py`에 자리 마련)
- 스크린샷 분석(Playwright + 멀티모달 LLM): 로고가 이미지로만 있는 피싱 페이지 탐지
- 1단계 검증에서 놓친 피싱 유형 분석·보완
