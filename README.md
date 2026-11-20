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

검증·테스트 스크립트를 돌릴 때만 개발용 라이브러리를 추가로 설치합니다. 서버 실행에는 필요 없습니다.
```powershell
.\.venv\Scripts\python -m pip install --only-binary=:all: --require-hashes -r requirements-dev.txt
```

| 환경변수 (.env) | 어디서 발급 | 없으면 |
|---|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) | AI 사칭 판별이 "AI 분석 불가"로 표시되고 규칙만으로 판정 |
| `SAFE_BROWSING_API_KEY` | Google Cloud 콘솔 → Safe Browsing API | DB 조회가 "확인 불가"로 표시 |
| `TYPESAFE_API_KEY` | [TypeSafe 콘솔](https://console.typesafe.ai/keys) (Jev, 유료·입력 100만 토큰당 $0.042) | QR 목적 비교를 Gemini가 대신 판정 |

`.env`는 Git에 올라가지 않습니다. 키를 코드나 문서에 직접 적지 마세요.

## 2. 실행

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```
브라우저에서 <http://localhost:8000> 접속 → 첫 화면에서 바로 카메라가 켜집니다. (모바일 세로 화면 기준)
사진 올리기·링크 직접 입력은 카메라 아래 **"다른 방법으로 검사"** 메뉴에 있습니다.

- **카메라**는 보안 정책상 `localhost` 또는 `https` 주소에서만 켜집니다. 권한이 없으면 "카메라로 QR 스캔" 버튼이 대신 보입니다.
- **휴대폰으로 시연하기**: 휴대폰에서 `http://PC주소:8000`으로 접속하면 카메라가 안 켜지므로, 아래 **배포(Render)** 의 https 주소로 접속합니다.
  - 시연에는 실제 피싱 주소 대신 가짜 주소·시연용 QR을 씁니다.

### 배포 (Render)

저장소 루트의 `render.yaml`에 배포 설정(설치·실행 명령, 파이썬 3.12, 필요한 환경변수 이름)이 들어 있습니다.
1. <https://render.com> 가입 → GitHub 계정 연결
2. 대시보드 **New → Blueprint** → 이 저장소 선택 → `render.yaml`이 자동으로 읽힘
3. 환경변수 입력 칸에 `GEMINI_API_KEY`, `SAFE_BROWSING_API_KEY`, `TYPESAFE_API_KEY` 값 입력 (키는 코드·커밋에 넣지 않음)
4. 배포가 끝나면 `https://qr-phishing-analyzer-xxxx.onrender.com` 같은 주소가 생김 → `주소/api/health`에서 `"ai": true`면 키 정상
- `main`에 push하면 자동으로 다시 배포됩니다.
- 무료 요금제는 15분 동안 접속이 없으면 잠들고, 다음 접속 때 깨어나는 데 30초~1분 걸립니다. **시연 직전에 한 번 접속**해 깨워 둡니다.
- 무료 서버는 재시작하면 저장 파일(`data/uploads`, AI 캐시 등)이 지워집니다. 분석 동작에는 문제없습니다.
- 시연용 QR 이미지: `python scripts/make_demo_qr.py` → `demo/` 폴더 (정상 1개, Google 공식 테스트 피싱 주소 1개, 두 개가 함께 있는 이미지)

### PWA 확인 방법 (로컬, 폰 설치)

이 서비스는 **PWA(설치형 웹앱)** 입니다. 기능은 웹과 같고, 홈 화면 아이콘으로 주소창 없이 전체 화면으로 열립니다.
- `static/manifest.webmanifest`: 앱 이름·아이콘·전체 화면 설정 (브라우저가 "설치 가능한 앱"으로 인식)
- `static/sw.js` (서버 주소 `/sw.js`): 서비스워커. 화면 파일만 "네트워크 우선, 실패하면 캐시"로 저장. `/api/*`와 외부 도메인(jsQR, 구글 폰트)은 캐시하지 않음
- 화면 파일 목록을 바꾸면 `sw.js`의 `VERSION`을 올립니다. (옛 캐시는 자동 삭제)
- 서비스워커·카메라는 `localhost` 또는 `https` 주소에서만 동작합니다.

**로컬(PC, Chrome/Edge)에서 확인**
1. 서버 실행 후 <http://localhost:8000> 접속 → `F12`(개발자도구) → **Application** 탭
2. **Manifest**: 이름 "QR 피싱 분석기", 아이콘 3개(192·512·maskable), Display `standalone`이 보이고 경고가 없으면 정상
3. **Service workers**: `http://localhost:8000/` 범위에 `sw.js`가 **activated and is running** 이면 정상
4. **Storage → Cache storage → `qr-shell-v1`**: `/`, `app.js`, `style.css`, 아이콘 등 화면 파일만 있고 **`/api/...` 항목이 없어야** 정상
5. 검사를 한 번 실행한 뒤 **Network** 탭에서 `analyze` 요청의 Size 칸이 `(ServiceWorker)`나 `(disk cache)`가 아니라 실제 바이트 수로 나오면 캐시를 거치지 않은 것
6. (선택) 주소창 오른쪽 **설치 아이콘**을 누르면 PC 앱 창으로 열림
- 수정한 JS·CSS가 안 바뀌어 보이면: Application → Service workers → **Update on reload** 체크 후 새로고침

**폰에 설치 (https 배포 주소에서)**
- Android(Chrome): 주소 접속 → 메뉴(⋮) → **앱 설치**(또는 "홈 화면에 추가")
- iPhone(Safari): 주소 접속 → 공유 버튼 → **홈 화면에 추가**
- 홈 화면 아이콘으로 열었을 때 주소창 없이 전체 화면이면 성공. 카메라 권한은 처음 한 번 다시 물어봅니다.

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

### OCR 테스트 (2단계 준비: 안내문 사진 읽기)

QR 안내문 사진을 Gemini가 얼마나 잘 읽는지 측정합니다. `requirements-dev.txt` 설치가 필요합니다.
```powershell
.\.venv\Scripts\python scripts\ocr_test.py make   # 합성 안내문 11종 × 촬영 조건 7가지 = 77장 → data/ocr_samples/
.\.venv\Scripts\python scripts\ocr_test.py run    # Gemini로 읽고 채점 → eval/ocr_results.md (약 8분, 응답은 data/ocr_raw/ 에 저장해 재사용)
.\.venv\Scripts\python scripts\ocr_test.py view   # 시연 페이지 → data/ocr_samples/viewer.html (사진 옆에 정답·AI 답 비교)
```
- 안내문 정답: `eval/ocr_cases.json` (기관명은 가상, QR 주소는 접속 불가한 `.example` 도메인)
- 실제 사진 추가: `data/ocr_samples/real/` 에 사진과 같은 이름의 정답 `.json`을 넣고 `run` → 같은 표에 추가됨
  예: `{"text": "안내문 글자 전부", "purpose": "주차요금 결제", "organization": "OO공단", "payment": true, "sticker": false}`
- `run` 중에 `view`를 실행하면 페이지가 10초마다 새로고침되며 진행 상황을 보여 줍니다.

### Jev vs Gemini 비교 (맥락 일치 판정)

`.env`에 `TYPESAFE_API_KEY`가 필요합니다. 위 OCR 테스트(`make`, `run`)를 먼저 한 번 실행해 둡니다.
```powershell
.\.venv\Scripts\python scripts\jev_test.py   # → eval/jev_results.md, 시연 페이지 data/ocr_samples/jev_viewer.html
```
- A. 안내문 사진 77장을 Jev에 직접 넣어 안내 목적·결제 요구·스티커 의심을 Gemini 결과와 비교
- B. `eval/context_cases.json`의 (안내문, 접속 페이지) 39쌍으로 맥락 일치 판정: Gemini(글자) / Jev(글자) / Jev(사진)
- Jev 요금: 입력 100만 토큰당 $0.042, 출력 무료. 전체 1회 실행 약 $0.015. 응답은 `data/jev_raw/`에 저장해 재사용

## 4. 테스트

```powershell
.\.venv\Scripts\python -m pytest
```

## 5. 판정 방식

| 등급 | 예시 | 반영 |
|---|---|---|
| 확정 신호 | Safe Browsing·OpenPhish 피싱 DB 등재 | 점수와 관계없이 **위험** |
| 강한 신호 | 브랜드 유사 도메인, 30일 미만 신규 도메인, IP 주소 URL, 단축 URL이 다른 도메인으로 이동, 브랜드와 도메인이 다른 로그인 폼, **AI 사칭 판별**, **QR 안내문과 페이지 목적 불일치** | 높은 배점 |
| 약한 신호 | http, 긴 URL, 서브도메인·파라미터·인코딩 과다, 악용 잦은 TLD, 무료 호스팅 주소 | 낮은 배점, **합계 상한**. 약한 신호만으로는 최대 **경고** |
| 안전 신호 | 신뢰 사이트 목록, 정상 인증서 | 감점 (확정 신호는 상쇄 못 함) |
| 조합 규칙 | http + 비밀번호 입력, 신규 도메인 + 브랜드 유사, 단축 URL + 신규 도메인 | 추가 가중 |

- **판정 위계** (`config/rules_hierarchy.json`): 범죄유형(지금은 피싱) > 예방 수칙(대분류: AI 분석·주소 확인·정보 입력·앱·파일 설치·QR 출처·안전 근거) > 검사 묶음(중분류) > 검사 항목(소분류) > 위 점수 신호.
  점수는 위 표대로 계산하고, 결과 화면은 판정·**위험도(100점 만점 표시, 100점 넘으면 100)**·예방 수칙 한 줄·행동 안내를 먼저 보여 준 뒤, **점검 결과 트리**에서 검사 항목마다 위험/경고/안전/해당 없음을 보여 줍니다. 항목을 누르면 탐지 근거(문구·출처·점수)가 펼쳐집니다.
  대분류 이름·수칙 문구·중분류·항목 이름·행동 안내는 모두 이 파일에서 고칩니다. (2단계에서 범죄유형 추가로 확장)
- 판정 등급 표시: 30점 미만은 "안전"이라고 단정하지 않고 **정황 없음**(0점) / **정황 미약**(1~14점) / **확인 권장**(15~29점), 그 위로 **경고**(30점 이상) / 위험(60점 이상)
- **피싱 판정과 기타 위협은 따로 표시합니다.** 기타 위협·주의사항(악성코드 DB, 실행 파일 다운로드, http, 인증서 오류)은 피싱 판정을 바꾸지 않습니다. 단, 악성코드 DB 등재는 종합 판정을 위험으로 올리되 "악성코드 위험"으로 안내하고 피싱 판정은 따로 보여 줍니다.
- **AI 사칭 판별**: 서버가 페이지 HTML에서 제목·본문·입력란·이미지·링크 도메인을 뽑아 Gemini에 보내고, AI는 "어떤 서비스를 표방하는지 / 페이지 목적 / 요구 정보 / 공식 도메인 추정"만 답합니다. 실제 도메인과의 비교와 점수 반영은 규칙이 합니다. 피싱 DB 등재나 신뢰 사이트처럼 규칙으로 판정이 분명하면 AI를 호출하지 않습니다.
  일관성을 위해 고정 질문·고정 JSON 형식·temperature 0·seed 고정을 쓰고, 실시간 분석에서는 같은 URL의 결과를 24시간(`settings.json`의 `ai.url_cache_hours`) 재사용합니다.
- **QR 목적 비교 (2단계)**: QR을 찍은 카메라 화면(또는 올린 사진)에서 **Gemini가 안내문 글자·목적을 읽고**(사진이 올라오는 순간 미리 시작), **Jev가 '안내문 목적과 실제 페이지 목적·요구 정보가 일치하는가'를 확률로 판정**합니다. 불일치 확률 0.8 이상이면 강한 신호(40점), 0.5 이상이면 약한 신호(확인 권장, 10점), QR이 덧붙인 스티커로 보이면 약한 신호(15점), 스티커 + 불일치는 조합 규칙(+20점). 기준값은 `settings.json`의 `context`, 질문 문구 선택 근거는 `eval/jev_prompt_results.md`, 오타 내성은 `eval/jev_typo_results.md`. Jev가 실패하면 Gemini가 대신 판정하고, 둘 다 실패하면 '확인 불가'로 표시한 채 나머지 분석은 그대로 진행합니다. 링크를 직접 입력하면(사진 없음) 비교하지 않습니다.
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
  notice.py       QR 안내문 읽기 (Gemini OCR, 사진 축소·캐시·미리 읽기)
  context.py      QR 안내문 목적 vs 페이지 목적 비교 (Jev, 실패 시 Gemini)
  jev.py          Jev(TypeSafe) 호출
  uploads.py      카메라 프레임·업로드 사진 저장 위치
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

- ✅ QR 안내문 맥락 일치 분석: Gemini OCR + Jev 목적 비교 (위 5. 판정 방식). 실제 안내문 사진으로 사진 크기·기준값 확정 예정
- 스크린샷 분석(Playwright + 멀티모달 LLM): 로고가 이미지로만 있는 피싱 페이지 탐지
- 1단계 검증에서 놓친 피싱 유형 분석·보완
