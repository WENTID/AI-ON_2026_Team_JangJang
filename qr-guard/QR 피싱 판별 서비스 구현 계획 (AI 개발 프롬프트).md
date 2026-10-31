# QR 피싱 판별 서비스 구현 계획 (AI 개발 프롬프트)

Oct 31, 2026 · @Team.장장

## 0. 사용법 (팀원용)

아래 1\~11장을 통째로 복사해 AI 코딩 도구(Claude Code, Cursor 등)에 붙여넣고, 맨 앞에 "이 명세대로 10장의 1단계만 구현하고 멈춰줘"라고 적으세요. 한 번에 전부 만들게 하면 어디서 틀렸는지 찾기 어렵습니다.

- 단계마다 완료 기준을 직접 확인한 뒤 다음 단계를 요청하세요.
- AI가 짠 코드는 최소한 한 명이 읽고 설명할 수 있어야 합니다. 심사에서 질문받습니다.
- API 키는 `.env`에만 두고 절대 커밋하지 마세요.
- 정답셋(7장)은 AI에게 맡기지 말고 팀이 직접 만드세요. 평가 기준을 AI가 정하면 결과를 믿기 어렵습니다.

## 1. 프로젝트 개요

너는 이 프로젝트의 개발자다. QR 코드를 찍기 전에, QR 옆에 적힌 안내 문구와 실제 연결 페이지를 비교해 피싱 위험을 근거와 함께 판정하는 웹 데모를 만든다. 교내 AI 경진대회 출품작이며, 팀원은 프로젝트 경험이 적으므로 코드는 단순하고 주석을 충분히 달아야 한다.

핵심 원칙은 세 가지다.

1. "목적이 달라 보인다"는 이유만으로 피싱이라고 단정하지 않는다. 예: 주차요금 결제에 로그인이 필요한 것은 정상일 수 있다.
2. 판정은 "안내된 목적에 비해 과도한 정보를 요구하는가"와 "의심할 만한 불일치가 있는가"를 근거로 내리고, 근거를 반드시 함께 보여준다.
3. 정보가 부족하면 판정을 유보(UNKNOWN)한다.

### 이번에 만들 것

- QR 이미지 또는 URL과 안내 문구를 입력받아 판정하는 웹 화면
- 리다이렉트 추적, 페이지 텍스트·입력란 추출, LLM 판정
- 비교용 "URL만 검사" 규칙 판정기(baseline)
- 가짜 테스트 페이지 서버와 시나리오 정답셋
- 두 방식을 비교하는 평가 스크립트

### 이번에 만들지 않을 것 (고도화 단계)

- 모바일 앱 (웹 데모로 대체)
- 화면 스크린샷을 이미지로 분석하는 기능
- 여러 페이지에 걸쳐 정보를 요구하는 흐름 분석
- 실제 피싱 DB 연동, 회원가입·로그인

## 2. 기술 스택과 폴더 구조

초보 팀이 다루기 쉽도록 전부 Python으로 만든다.

| 용도 | 사용 도구 | 비고 |
| --- | --- | --- |
| 언어 | Python 3.11 |  |
| 화면 | Streamlit | HTML/JS 없이 데모 화면 제작 |
| QR 해독 | opencv-python (`cv2.QRCodeDetector`) | 별도 시스템 라이브러리 불필요 |
| 리다이렉트 추적 | httpx | 자동 리다이렉트 끄고 직접 한 단계씩 따라감 |
| HTML 분석 | beautifulsoup4 | 본문 텍스트, form, input 추출 |
| 도메인 분리 | tldextract | 등록 도메인·서브도메인 분리 |
| LLM | anthropic SDK (또는 팀이 쓰는 API) | 모델명은 `.env`의 `LLM_MODEL`로 설정 |
| 가짜 페이지 서버 | Flask | 테스트용 페이지를 로컬에서 제공 |
| 평가 | pandas | 결과표·지표 계산 |

LLM 호출은 `analyzer.py` 한 곳에만 두어, 다른 API로 바꾸더라도 그 파일만 고치면 되게 한다.

```
qr-guard/
├── app.py                 # Streamlit 화면
├── core/
│   ├── qr_decode.py       # QR 이미지 → URL
│   ├── url_trace.py       # 리다이렉트 추적
│   ├── url_features.py    # URL 특징 추출
│   ├── page_extract.py    # 페이지 텍스트·요구 정보 추출
│   ├── baseline.py        # URL만 보는 규칙 판정
│   ├── analyzer.py        # LLM 판정
│   └── pipeline.py        # 전체를 연결하는 함수
├── prompts/
│   └── judge_prompt.md    # LLM 판정 프롬프트 (코드와 분리)
├── mock_site/
│   ├── server.py          # Flask 가짜 페이지 서버
│   └── pages/             # 시나리오별 HTML
├── data/
│   ├── scenarios.json     # 정답셋
│   └── brands.json        # 유명 브랜드·기관 도메인 목록
├── eval/
│   └── run_eval.py        # 두 방식 비교 평가
├── tests/                 # 모듈별 간단한 pytest
├── .env.example
├── requirements.txt
└── README.md              # 설치·실행 방법
```

## 3. 전체 처리 흐름

`pipeline.py`의 `analyze(qr_image=None, url=None, notice_text="", test_mode=False)` 하나가 아래 순서를 실행하고 결과 딕셔너리를 반환한다. 화면과 평가 스크립트는 모두 이 함수만 호출한다.

1. **입력**: QR 이미지가 있으면 `qr_decode`로 URL을 얻고, 없으면 입력된 URL을 쓴다. 안내 문구(notice\_text)는 사용자가 QR 주변 글자를 입력한 것이며 비어 있을 수 있다.
2. **리다이렉트 추적**: `url_trace`로 최종 URL까지 따라가며 경로를 기록한다.
3. **URL 특징 추출**: `url_features`로 최초 URL과 최종 URL의 특징을 뽑는다.
4. **baseline 판정**: `baseline`이 URL 특징만으로 판정한다. 화면에서 비교용으로 함께 보여준다.
5. **페이지 추출**: `page_extract`로 최종 페이지의 제목, 본문 요약, 입력란, 요구 정보 종류, 앱 설치 유도, 압박 문구를 뽑는다.
6. **LLM 판정**: `analyzer`가 안내 문구 + URL 특징 + 페이지 추출 결과를 LLM에 넘겨 JSON 판정을 받는다.
7. **후처리 규칙**: 페이지를 못 불러왔거나 LLM 응답이 형식에 맞지 않으면 판정을 UNKNOWN으로 바꾸고 사유를 남긴다. 앱 설치 파일(APK) 다운로드로 이어지면 LLM 결과와 관계없이 최소 CAUTION 이상으로 둔다.
8. **출력**: 최종 판정, 근거 목록, baseline 결과, 리다이렉트 경로, 추출 정보를 한 딕셔너리로 반환한다.

판정 등급은 4가지다.

| 등급 | 화면 표시 | 의미 |
| --- | --- | --- |
| SAFE | 안전 | 안내 목적과 페이지가 일치하고 과도한 요구가 없음 |
| CAUTION | 주의 | 의심 신호가 있으나 정상일 가능성도 있음 |
| DANGER | 위험 | 목적에 비해 과도한 민감정보 요구, 또는 명백한 위장·불일치 |
| UNKNOWN | 판단 보류 | 판단할 정보가 부족함 (사유를 함께 표시) |

## 4. 모듈별 상세 명세

모든 함수는 예외를 밖으로 던지지 말고, 실패 시 `error` 필드에 사유를 담아 반환한다. 그래야 화면과 평가가 중간에 멈추지 않는다.

### 4-1. qr\_decode.py

- `decode_qr(image_bytes) -> {"url": str | None, "raw": str | None, "error": str | None}`
- QR이 없거나 내용이 URL이 아니면 url은 None, raw에 원문을 담는다.

### 4-2. url\_trace.py

- `trace(url, test_mode=False, override=None) -> {"chain": [...], "final_url": str, "final_status": int, "content_type": str, "is_download": bool, "error": str | None}`
- httpx에서 `follow_redirects=False`로 두고 최대 10번까지 Location 헤더를 직접 따라간다. chain의 각 항목은 `{"url", "status"}`다.
- HTML `<meta http-equiv="refresh">`와 단순한 `location.href = "..."` 스크립트 리다이렉트도 감지해 따라간다.
- 타임아웃 5초, 요청 당 본문 최대 1MB만 읽는다.
- Content-Type이 `application/vnd.android.package-archive`이거나 URL이 `.apk`로 끝나면 본문을 내려받지 않고 `is_download=True`로 표시한다.
- **테스트 모드**: `override`는 `{"표시용 URL": "실제 접속할 로컬 URL"}` 딕셔너리다. 테스트 모드에서는 요청할 URL을 override로 바꿔 가짜 서버에 접속하되, chain과 final\_url에는 표시용 URL을 기록한다. 가짜 서버가 돌려주는 Location도 표시용 URL이며 같은 방식으로 바꾼다. 이렇게 해야 실제 피싱 사이트 없이도 "수상한 도메인"을 재현할 수 있다.

### 4-3. url\_features.py

- `extract_features(url, brands) -> dict`
- 추출 항목: 등록 도메인, TLD, https 여부, IP 주소 사용 여부, punycode(`xn--`) 포함 여부, 서브도메인 깊이, URL 길이, `@` 포함 여부, 단축 URL 서비스 여부(bit.ly, han.gl, me2.do 등 목록 기반), 저가 TLD 여부(xyz, top, click 등 목록 기반)
- 유사 도메인: `brands.json`의 각 도메인과 편집 거리(Levenshtein) 1\~2이거나, 브랜드의 영문 키워드가 도메인 안에 들어 있지만 공식 도메인이 아닌 경우 `lookalike_of`에 브랜드명을 적는다.
- `brands.json` 형식: `[{"name": "카카오페이", "keywords": ["kakaopay"], "domains": ["kakaopay.com"]}, ...]`. 초기 목록은 팀이 채운다. 전국 지자체·주차장까지 목록에 넣을 수는 없다는 점이 URL 검사의 실제 한계이므로, 가상의 새솔시는 목록에 넣지 않는다.

### 4-4. page\_extract.py

- `extract_page(html, final_url) -> dict`
- 추출 항목:
  - `title`, `text_summary` (보이는 텍스트 앞 1,500자)
  - `forms`: 각 form의 action URL과 그 도메인이 현재 페이지와 같은지 여부
  - `inputs`: 각 input의 type, name, id, placeholder, 연결된 label 텍스트
  - `requested_info`: inputs를 아래 범주로 분류한 목록 (키워드 규칙, 한글·영문 모두)
  - `app_install_links`: `.apk` 링크나 "앱 설치", "보안 프로그램" 문구
  - `urgency_phrases`: "과태료", "미납", "즉시", "분 이내", "정지" 등

| requested\_info 범주 | 예시 키워드 |
| --- | --- |
| card\_number | 카드번호, card, cardno |
| card\_cvc | CVC, CVV, 보안코드 |
| card\_password | 카드 비밀번호, 앞 2자리 |
| resident\_id | 주민등록번호, 주민번호, 생년월일+뒷자리 |
| bank\_account | 계좌번호, 은행, 계좌 비밀번호 |
| otp\_cert | OTP, 인증번호, 공동인증서, 보안카드 |
| login | 아이디, 비밀번호, password |
| phone | 휴대폰, 전화번호 |
| name\_address | 이름, 주소 |
| vehicle | 차량번호 |
| other | 위에 해당하지 않는 입력란 |

### 4-5. baseline.py

- `judge_url_only(features) -> {"verdict": str, "reasons": [str]}`
- 규칙은 6장을 따른다.

### 4-6. analyzer.py

- `judge_with_llm(notice_text, url_info, page_info) -> dict` (형식은 5장)
- 프롬프트는 `prompts/judge_prompt.md`에서 읽는다. 입력은 JSON 문자열로 넣는다.
- 응답에서 코드블록 표시(\`\`\`)를 제거한 뒤 JSON으로 읽고, 필수 필드가 없으면 한 번 재시도한다. 그래도 실패하면 UNKNOWN을 반환한다.
- temperature는 0으로 두어 평가 결과가 최대한 재현되게 한다.
- 평가 때 API 비용을 아끼도록 입력 해시별로 결과를 `cache/` 폴더에 저장하는 옵션을 둔다.

## 5. LLM 판정 프롬프트와 출력 형식

아래 내용을 `prompts/judge_prompt.md`로 저장하고, `{INPUT_JSON}` 자리에 입력을 넣는다. 팀은 평가 결과를 보며 이 파일을 고쳐 성능을 개선한다.

```markdown
너는 QR 코드 피싱(큐싱)을 검사하는 보안 분석가다.
사용자가 QR 주변에서 본 안내 문구, 접속 URL 정보, 최종 페이지에서 추출한 정보가 주어진다.

## 판단 순서
1. 안내 문구에서 QR의 목적을 추론하라 (예: 공영주차장 요금 결제, 식당 메뉴 주문).
   안내 문구가 없으면 "목적 불명"으로 둔다.
2. 최종 페이지의 실제 목적을 추론하라.
3. 그 목적을 달성하는 데 통상 필요한 정보와, 페이지가 실제로 요구하는 정보를 비교하라.
4. 아래 의심 신호를 점검하라.
   - 과도한 요구: 목적에 비해 카드 비밀번호, 주민등록번호 전체, OTP, 계좌 비밀번호 등을 요구
   - 불일치: 안내된 기관·브랜드와 도메인이 무관하거나 유사 도메인, 페이지는 브랜드를 사칭하는데 도메인은 다름,
     form이 다른 도메인으로 전송됨
   - 위험 행동 유도: 앱(APK) 설치, 원격제어 앱 설치
   - 압박 문구: 과태료, 미납, 시간 제한
5. 등급을 정하라.

## 중요한 규칙
- 로그인을 요구한다는 사실만으로 위험이라고 하지 마라. 결제·예약 서비스는 로그인을 요구할 수 있다.
- 도메인이 낯선 것만으로 위험이라고 하지 마라. 작은 가게는 새 도메인을 쓸 수 있다.
- 모든 판단에는 입력에 실제로 있는 내용을 근거로 들어라. 입력에 없는 사실을 지어내지 마라.
- 페이지 정보가 없거나, 안내 문구가 없고 뚜렷한 신호도 없으면 UNKNOWN을 선택하고 어떤 정보가 더 필요한지 적어라.

## 등급
- SAFE: 목적과 페이지가 일치하고 과도한 요구나 불일치가 없다.
- CAUTION: 의심 신호가 있지만 정상일 가능성도 크다.
- DANGER: 목적에 비해 과도한 민감정보를 요구하거나, 명백한 위장·불일치가 있다.
- UNKNOWN: 판단할 정보가 부족하다.

## 출력
아래 JSON만 출력하라. 설명이나 코드블록 표시는 넣지 마라.

## 입력
{INPUT_JSON}
```

LLM이 돌려줄 JSON 형식은 다음과 같다. 프롬프트의 "출력" 아래에도 이 예시를 그대로 넣는다.

```json
{
  "verdict": "SAFE | CAUTION | DANGER | UNKNOWN",
  "notice_purpose": "안내 문구로 본 QR의 목적 또는 '목적 불명'",
  "page_purpose": "페이지의 실제 목적",
  "expected_info": ["이 목적에 통상 필요한 정보"],
  "requested_info": ["페이지가 실제로 요구하는 정보"],
  "excessive_requests": ["목적에 비해 과도한 요구"],
  "mismatches": ["발견한 불일치"],
  "evidence": ["판정 근거, 사용자에게 보여줄 문장 1~4개"],
  "missing_info": ["UNKNOWN일 때 더 필요한 정보"],
  "advice": "사용자가 할 행동 한 문장"
}
```

## 6. URL만 검사하는 비교 기준 (baseline)

baseline은 "기존 URL 검사기가 하는 수준"을 흉내 낸 점수 규칙이다. 페이지 내용과 안내 문구는 보지 않는다. 최초 URL과 최종 URL 중 점수가 높은 쪽을 쓴다.

| 조건 | 점수 |
| --- | --- |
| 유사 도메인(lookalike\_of 있음) | +3 |
| IP 주소로 접속 | +3 |
| punycode 포함 | +2 |
| URL에 `@` 포함 | +2 |
| 저가 TLD | +1 |
| https 아님 | +1 |
| 단축 URL 사용 | +1 |
| 서브도메인 깊이 3 이상 | +1 |
| URL 길이 100자 이상 | +1 |
| `.apk` 다운로드 | +3 |

합계 3점 이상은 DANGER, 1\~2점은 CAUTION, 0점은 SAFE다. baseline은 UNKNOWN을 내지 않는다. 점수를 받은 조건은 reasons에 모두 적는다. 점수와 기준값은 `baseline.py` 맨 위에 상수로 모아 쉽게 고칠 수 있게 한다.

baseline을 일부러 약하게 만들면 안 된다. 평가에서 "URL 검사도 이 정도는 잡는다, 그런데 이건 못 잡는다"를 공정하게 보여줘야 심사에서 설득력이 있다.

## 7. 시나리오 정답셋과 가짜 테스트 페이지

정답셋은 팀이 직접 작성하며, 최종 15\~20개를 목표로 한다. AI는 아래 8개 예시로 `scenarios.json`과 가짜 페이지를 만들고, 팀이 나머지를 추가한다. 시나리오의 도메인과 기관명(예: 새솔시)은 모두 가상이다.

### scenarios.json 항목 형식

```json
{
  "id": "P1",
  "category": "phishing | normal | ambiguous",
  "title": "주차장 QR 위 덟붙인 스티커",
  "notice_text": "새솔시 공영주차장 주차요금 QR 결제",
  "qr_url": "https://saesol-parking.com/pay",
  "override": {"https://saesol-parking.com/pay": "http://127.0.0.1:5001/p1/pay"},
  "expected_verdict": "DANGER",
  "acceptable_verdicts": ["DANGER"],
  "expected_points": ["주차요금에 비해 카드 비밀번호·주민번호 요구는 과도함"],
  "baseline_expected": "SAFE",
  "note": "URL은 멀충해 보여 baseline은 놓침"
}
```

`acceptable_verdicts`는 애매한 사례에서 여러 등급을 정답으로 인정하기 위한 것이다. `expected_points`는 근거에 이 내용이 들어가야 한다는 사람용 메모로, 평가 때 팀이 눈으로 확인한다.

### 예시 시나리오 8개

| ID | 유형 | 안내 문구 | QR URL → 최종 URL | 페이지가 요구하는 정보 | 정답 (허용) | baseline 예상 | 핵심 근거 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| N1 | 정상 | 새솔시 공영주차장 요금 결제 | parking.saesol.go.kr/pay | 아이디·비밀번호 로그인, 차량번호 | SAFE | SAFE | 로그인은 결제 서비스에서 정상적인 요구 (로그인 오판 방지 사례) |
| N2 | 정상 | 테이블 주문 QR, 테이블 7번 | han.gl/ab12 → order.minttable.xyz/t/7 | 메뉴 선택, 요청사항 | SAFE | CAUTION | 단축 URL·저가 TLD지만 민감정보 요구 없음 (과잉 경고 감소 사례) |
| P1 | 피싱 | 새솔시 공영주차장 요금 결제 | saesol-parking.com/pay | 카드번호, CVC, 카드 비밀번호, 주민번호 | DANGER | SAFE | 주차요금에 비해 과도한 요구, 시청 도메인 아님 (baseline이 놓치는 사례) |
| P2 | 피싱 | 택배 주소 확인 후 재배송 | delivery-check.top/app | APK 다운로드 | DANGER | DANGER | 배송 확인에 앱 설치 불필요 |
| P3 | 피싱 | 카카오페이 간편결제 | kakaopay-secure.net/login | 로그인, OTP | DANGER | DANGER | 브랜드 사칭 유사 도메인 |
| P4 | 피싱 | 아파트 관리비 미납 안내, 오늘까지 납부 | aptpay.kr/bill | 은행·계좌번호, 계좌 비밀번호, 주민번호 | DANGER | SAFE | 압박 문구 + 계좌 비밀번호 요구 (baseline이 놓치는 사례) |
| A1 | 애매 | (없음) | me2.do/x9 → shop-daily.co.kr/login | 로그인 | UNKNOWN (CAUTION) | CAUTION | 목적을 알 수 없어 판단 보류가 적절 |
| A2 | 애매 | 카페 무료 Wi-Fi 연결 | wifi.cafe-onsae.kr/join | 휴대폰 번호, 마케팅 동의 | CAUTION (SAFE) | SAFE | 연결에 휴대폰 번호까지 필요한지 애매함 |

팀이 추가할 때는 정상·피싱·애매를 비슷한 비율로 맞추고, "baseline이 틀리는 사례"와 "둘 다 맞히는 사례"를 모두 넣는다. 우리 방식에 유리한 사례만 넣으면 평가를 믿을 수 없다.

### 가짜 페이지 서버 (mock\_site)

- Flask로 `127.0.0.1:5001`에서 띄운다. 시나리오마다 `/n1/...`, `/p1/...`처럼 경로를 나눈다.
- 리다이렉트가 있는 시나리오는 302 응답의 Location에 "표시용 URL"을 넣고, override가 이를 다시 로컬 경로로 바꾼다.
- 피싱 페이지는 실제처럼 보이게 만들되, form의 action은 `#`이고 제출 버튼은 아무 동작도 하지 않는다. 입력값을 저장하거나 전송하는 코드는 넣지 않는다.
- APK 시나리오는 실제 파일 없이 Content-Type 헤더만 돌려준다.
- 각 페이지 구석에 "테스트용 가짜 페이지"라고 HTML 주석을 단다.

## 8. 평가 스크립트와 지표

`python eval/run_eval.py`는 가짜 서버가 켜져 있는지 확인한 뒤, 정답셋 전체를 테스트 모드로 돌려 baseline과 우리 방식을 비교한다. 결과는 `eval/results/`에 날짜별로 저장해 프롬프트 수정 전후를 비교할 수 있게 한다.

지표는 다음 5가지를 두 방식 모두 계산한다.

| 지표 | 계산 | 보여주는 것 |
| --- | --- | --- |
| 정답률 | 판정이 acceptable\_verdicts에 들어간 비율 (전체) | 전반적 성능 |
| 피싱 탐지율 | 피싱 사례 중 DANGER 또는 CAUTION인 비율 | 놓치지 않는가 |
| 피싱 정확 탐지율 | 피싱 사례 중 DANGER인 비율 | 확실히 경고하는가 |
| 정상 오탐률 | 정상 사례 중 DANGER 또는 CAUTION인 비율 | 정상을 의심하지 않는가 (낮을수록 좋음) |
| 애매 사례 적절 처리율 | 애매 사례 중 판정이 acceptable\_verdicts에 들어간 비율 | 모를 때 유보하는가 |

출력물은 세 가지다.

1. **사례별 결과표** (`cases.csv`, `cases.md`): ID, 유형, 정답, baseline 판정, 우리 판정, 각각 맞았는지, 우리 판정의 evidence. 사람이 근거가 expected\_points와 맞는지 확인할 수 있도록 나란히 보여준다.
2. **요약표** (`summary.md`): 위 5개 지표를 두 방식 비교로.
3. **차이 사례 목록** (`diff.md`): "baseline은 틀리고 우리는 맞힌 사례"와 "baseline은 맞고 우리는 틀린 사례"를 따로 모은다. 발표에서 "URL 검사보다 무엇을 더 발견하고 어떤 오판을 줄였는지"를 보여주는 핵심 자료다. 우리가 틀린 사례도 숨기지 않고 발표에 포함한다.

LLM 결과가 매번 조금씩 달라질 수 있으므로 `--repeat 3` 옵션으로 같은 사례를 3번 돌려 판정이 바뀐 사례를 표시하는 기능도 둔다.

## 9. 화면 구성

Streamlit 한 페이지에 위에서 아래로 배치한다. 시연에서 심사위원이 근거를 한눈에 볼 수 있는 것이 가장 중요하다.

1. **입력 영역**
   - 탭 2개: "QR 이미지 업로드" / "URL 직접 입력"
   - "QR 주변 안내 문구" 텍스트 입력란 (비워도 됨)
   - "테스트 시나리오 불러오기" 드롭다운: 선택하면 해당 시나리오의 안내 문구·URL·override가 채워지고 테스트 모드로 동작한다. 시연은 이 기능으로 한다.
   - "검사하기" 버튼
2. **판정 카드**: 등급을 크게(안전 초록, 주의 주황, 위험 빨강, 판단 보류 회색) 표시하고, 그 아래 evidence를 문장 목록으로, 맨 아래 advice를 보여준다. 색만으로 구분하지 않고 등급 글자를 함께 쓴다.
3. **목적 비교표**: "안내된 목적 / 실제 페이지 목적", "보통 필요한 정보 / 실제 요구 정보"를 두 열로 나란히 보여주고, excessive\_requests는 강조 표시한다. 이 표가 우리 서비스의 차별점을 보여주는 핵심 화면이다.
4. **URL만 검사했을 때**: baseline 판정과 이유를 작은 카드로 보여주고, 우리 판정과 다르면 "판정이 다릅니다"라고 표시한다.
5. **상세 정보 (접기/펼치기)**: 리다이렉트 경로, URL 특징표, 추출된 입력란 목록, LLM 원본 JSON.

주의 문구를 화면 하단에 고정한다: "이 결과는 참고용이며, 의심되면 결제나 정보 입력 전에 공식 앱이나 기관 대표번호로 확인하세요."

## 10. 단계별 구현 순서와 완료 기준

한 단계가 끝날 때마다 멈추고, 만든 파일과 확인 방법을 요약해 보고한다. 다음 단계는 요청받은 뒤에 시작한다.

| 단계 | 만들 것 | 완료 기준 |
| --- | --- | --- |
| 1. 뼈대 | 폴더 구조, requirements.txt, .env.example, README, 빈 함수들 | `pip install -r requirements.txt` 후 `streamlit run app.py`로 빈 화면이 뜨고, `pytest`가 실행됨 |
| 2. 가짜 서버와 정답셋 | mock\_site, 예시 8개 페이지, scenarios.json, brands.json | 서버를 켜고 브라우저에서 8개 페이지가 보이며, 리다이렉트·APK 시나리오가 의도대로 응답함 |
| 3. URL 분석 | qr\_decode, url\_trace, url\_features, baseline | 예시 QR 이미지에서 URL이 나오고, 8개 시나리오의 baseline 판정이 7장 표의 "baseline 예상"과 일치함 |
| 4. 페이지 추출 | page\_extract | P1에서 card\_number·card\_cvc·card\_password·resident\_id가, N1에서 login·vehicle이 추출됨 |
| 5. LLM 판정 | judge\_prompt.md, analyzer, pipeline | 커맨드라인에서 시나리오 ID를 넣으면 JSON 판정이 출력됨, API 키가 없으면 친절한 오류 메시지 |
| 6. 평가 | run\_eval.py | 8개 시나리오로 cases·summary·diff 파일이 생성됨 |
| 7. 화면 | app.py | 9장의 5개 영역이 모두 보이고, 시나리오 불러오기로 P1 시연이 됨 |
| 8. 다듬기 | 팀이 추가한 시나리오 반영, 프롬프트 개선 | 정답셋 15개 이상으로 평가하고 결과를 발표 자료에 넣을 수 있음 |

1\~4단계는 API 키 없이도 돌아가야 한다. LLM 비용 없이 기반을 먼저 검증하기 위해서다.

## 11. 안전 규칙과 팀 역할 분담

### 안전 규칙 (AI는 반드시 지킬 것)

- 개발·평가·시연은 가짜 페이지로만 한다. 실제 피싱 사이트를 수집하거나 접속하는 코드는 만들지 않는다.
- 실제 URL 검사 시에도 GET 요청만 보내고, form을 제출하거나 스크립트를 실행하거나 파일을 내려받지 않는다.
- 가짜 피싱 페이지는 입력값을 저장·전송하지 않으며, 서버는 127.0.0.1에서만 열린다.
- API 키는 `.env`에만 두고 `.gitignore`에 `.env`와 `cache/`를 넣는다.

### 팀 역할 분담 (팀원용)

서로 기다리지 않도록 세 갈래로 나눈다. 인원이 적으면 정답셋 담당이 발표 자료도 맡는다.

| 역할 | 맡는 일 | 주로 다루는 파일 |
| --- | --- | --- |
| 개발 | AI에게 단계별 구현 요청, 완료 기준 확인, 코드 설명 정리 | core/, app.py |
| 정답셋·평가 | 시나리오 15\~20개 작성, 가짜 페이지 내용 기획, 평가 결과 분석 | data/scenarios.json, mock\_site/pages/ |
| 프롬프트·조사 | 판정 프롬프트 개선, brands.json 작성, 유사 서비스 비교표 | prompts/, data/brands.json |

프롬프트 담당은 코드가 없어도 채팅창에서 5장 프롬프트에 시나리오를 넣어 바로 실험할 수 있다. 월요일 멘토링 전에는 1\~3단계와 정답셋 초안, 프롬프트 실험 결과 몇 건이면 충분하다.
