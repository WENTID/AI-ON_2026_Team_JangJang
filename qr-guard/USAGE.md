# 사용 방법 (팀원용)

처음 받는 사람 기준으로, 설치부터 시연·평가까지 순서대로 정리했습니다. 윈도우 PowerShell 기준입니다.

---

## 1. 처음 한 번만: 설치

### 1-1. Python 준비
- **Python 3.10 ~ 3.12**를 설치하세요. 3.13 이상은 opencv 설치가 안 될 수 있습니다.
- 설치된 버전 확인: `py -0`

### 1-2. 코드 받기
```powershell
git clone <이 저장소 주소>
cd <저장소 폴더>
```

### 1-3. 가상환경 만들고 패키지 설치
```powershell
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
> `.venv\Scripts\activate`로 가상환경을 켜도 되지만, PowerShell에서 "스크립트를 실행할 수 없습니다" 오류가 자주 납니다.
> 이 문서는 명령 앞에 `.venv\Scripts\`를 붙이는 방식으로 씁니다.

### 1-4. (선택) API 키 등록
LLM 판정(메인 판정)을 쓰려면 Anthropic API 키가 필요합니다.
```powershell
copy .env.example .env
notepad .env
```
`ANTHROPIC_API_KEY=` 뒤에 키를 붙여넣고 저장합니다. 키는 https://console.anthropic.com 에서 발급하며, 쓴 만큼 요금이 나옵니다.

> ⚠ `.env`는 절대 GitHub에 올리지 마세요. `.gitignore`에 들어 있어 보통은 자동으로 제외됩니다.

**키가 없으면?** 화면은 정상으로 뜨고, "URL만 검사했을 때" 결과와 상세 정보(리다이렉트 경로, 요구 입력란 등)는 나옵니다.
메인 판정은 "판단 보류"로 나옵니다.

---

## 2. 실행: 터미널 2개

### 터미널 1: 가짜 페이지 서버 (켜 둔 채로 두기)
```powershell
.venv\Scripts\python mock_site\server.py
```
`가짜 테스트 페이지 서버: http://127.0.0.1:5001`이 나오면 성공입니다.
브라우저에서 http://127.0.0.1:5001 에 들어가면 테스트 페이지 8개 목록이 보입니다.

### 터미널 2: 데모 화면
```powershell
.venv\Scripts\streamlit run app.py
```
브라우저에서 http://localhost:8501 이 열립니다.

> **처음 실행 시 `Email:`이라고 묻고 멈춥니다.** 그냥 **Enter**를 누르세요.
> 아예 묻지 않게 하려면: `.venv\Scripts\streamlit run app.py --server.headless true` (이때는 브라우저를 직접 여세요)

끌 때는 각 터미널에서 `Ctrl+C`.

---

## 3. 화면 사용법 (시연)

1. 맨 위 **"테스트 시나리오 불러오기"**에서 시나리오를 고릅니다. 예: `P1 · 주차장 QR 위 덧붙인 스티커`
   - 파란색 "테스트 모드" 안내가 뜨는지 확인하세요.
2. URL과 안내 문구가 자동으로 채워집니다.
   - QR로 시연하려면 "QR 이미지 업로드" 탭에 `data/qr_samples/P1.png`를 올리세요.
3. **검사하기** 클릭.

결과 화면 구성:
| 영역 | 내용 |
| --- | --- |
| 판정 카드 | 안전(초록) / 주의(주황) / 위험(빨강) / 판단 보류(회색) + 근거 + 권장 행동 |
| 목적 비교 | 안내된 목적과 실제 페이지 목적, 보통 필요한 정보와 실제 요구 정보 (과도한 요구는 빨간색) |
| URL만 검사했을 때 | baseline 판정과 이유. 우리 판정과 다르면 "판정이 다릅니다" 표시 |
| 상세 정보 | 리다이렉트 경로, URL 특징표, 추출된 입력란, LLM 원본 JSON |

### ❗ "서버에 연결할 수 없습니다 / 페이지를 불러오지 못했습니다"가 뜰 때
- **시나리오를 고르지 않고 검사한 경우**입니다. 시나리오 주소(예: `saesol-parking.com`)는 모두 **가상 도메인**이라 실제 인터넷에는 없습니다.
  시나리오를 고르면 테스트 모드가 켜지고, 가짜 서버로 접속합니다.
- 시나리오를 골랐는데도 뜨면 **터미널 1의 가짜 서버가 꺼져 있는지** 확인하세요.

### 예시 시나리오 8개
| ID | 유형 | 내용 | 정답 |
| --- | --- | --- | --- |
| N1 | 정상 | 시청 공영주차장 결제 (로그인 필요) | 안전 |
| N2 | 정상 | 식당 테이블 주문 (단축 URL + .xyz) | 안전 |
| P1 | 피싱 | 주차요금인데 카드 비밀번호·주민번호 요구 | 위험 |
| P2 | 피싱 | 택배 재배송 → APK 설치 유도 | 위험 |
| P3 | 피싱 | 카카오페이 사칭 유사 도메인 | 위험 |
| P4 | 피싱 | 관리비 미납 → 계좌 비밀번호 요구 | 위험 |
| A1 | 애매 | 안내 문구 없는 쇼핑몰 로그인 | 판단 보류 (주의도 인정) |
| A2 | 애매 | 카페 Wi-Fi에 휴대폰 번호 + 마케팅 동의 | 주의 (안전도 인정) |

추천 시연 순서: **P1**(URL 검사는 "안전"인데 우리는 "위험"), 그다음 **N1**(로그인을 요구해도 정상으로 판정), 그다음 **A1**(모르면 판단 보류).

---

## 4. 커맨드라인 도구 (가짜 서버는 켜 둔 상태)

```powershell
# 시나리오 하나 판정
.venv\Scripts\python core\pipeline.py P1
.venv\Scripts\python core\pipeline.py P1 --no-llm   # API 키 없이 (LLM 단계만 건너뜀)
.venv\Scripts\python core\pipeline.py P1 --full     # 추출 정보까지 전부 출력

# 전체 평가: baseline vs 우리 방식
.venv\Scripts\python eval\run_eval.py
.venv\Scripts\python eval\run_eval.py --repeat 3    # 3번씩 돌려 판정이 흔들리는 사례 표시
.venv\Scripts\python eval\run_eval.py --no-cache    # 저장된 LLM 결과를 무시하고 새로 호출

# 테스트
.venv\Scripts\python -m pytest
```

평가 결과는 `eval\results\<날짜_시간>\`에 저장됩니다.
- `summary.md`: 5개 지표 비교표 (정답률, 피싱 탐지율, 피싱 정확 탐지율, 정상 오탐률, 애매 사례 처리율)
- `cases.md` / `cases.csv`: 사례별 결과. 우리 근거가 기대 근거와 맞는지 **사람이 눈으로 확인**합니다.
- `diff.md`: "baseline은 틀리고 우리는 맞힌 사례"와 "그 반대"를 정리한 **발표용 핵심 자료**

> LLM 결과는 `cache/`에 저장돼 같은 입력이면 다시 호출하지 않습니다(비용 절약).
> 프롬프트를 고치면 입력이 달라지므로 자동으로 새로 호출됩니다.

---

## 5. 역할별로 고치는 파일

### 정답셋·평가 담당: 시나리오 추가하기
1. `mock_site\pages\`에 HTML 파일을 추가합니다. 기존 파일을 복사해서 고치면 쉽습니다.
   - form의 `action="#"`은 유지하고, 맨 위에 `<!-- 테스트용 가짜 페이지 -->` 주석을 남기세요.
2. `mock_site\server.py`에 경로를 추가합니다.
   ```python
   @app.route("/p5/pay")
   def p5_pay():
       return page("p5_pay.html")
   ```
3. `data\scenarios.json`에 항목을 추가합니다. `override`의 왼쪽은 "화면에 보일 가짜 주소", 오른쪽은 "실제로 접속할 로컬 주소"입니다.
4. `.venv\Scripts\python data\make_qr_samples.py`로 QR 이미지를 다시 만듭니다.
5. 서버를 재시작하고 `pytest`로 확인합니다. `baseline_expected`가 실제 baseline 결과와 다르면 테스트가 실패하니, 값을 확인해 고치세요.

> 정상·피싱·애매를 비슷한 비율로, baseline이 맞히는 사례도 꼭 넣으세요.

### 프롬프트·조사 담당
- `prompts\judge_prompt.md`: LLM 판정 프롬프트입니다. 고친 뒤 `eval\run_eval.py`로 전후를 비교하세요.
- `data\brands.json`: 사칭 탐지용 브랜드 목록입니다. 키워드는 4글자 이상으로 해야 오탐이 적습니다.

### 개발 담당
- 판정 기준 점수: `core\baseline.py` 맨 위 상수
- 입력란 분류 키워드: `core\page_extract.py`의 `CATEGORY_RULES`
- 모델 변경: `.env`의 `LLM_MODEL`, `LLM_EFFORT` (`low`로 하면 더 싸고 빠름)

---

## 6. 자주 생기는 문제

| 증상 | 해결 |
| --- | --- |
| `localhost:8501` 접속 안 됨 | Streamlit 터미널에 `Email:`이 떠 있으면 Enter. 또는 `--server.headless true`로 실행 |
| "서버에 연결할 수 없습니다" | 시나리오를 선택했는지, 가짜 서버(터미널 1)가 켜져 있는지 확인 |
| 판정이 전부 "판단 보류" | API 키 미등록. `.env` 확인 후 화면 재시작 |
| "API 키가 올바르지 않습니다" | `.env`의 키 앞뒤 공백·따옴표 확인 |
| `pip install` 중 opencv 오류 | Python 3.13 이상인지 확인. 3.10~3.12로 가상환경을 다시 만들기 |
| 스크립트를 실행할 수 없습니다 (activate) | activate를 쓰지 말고 `.venv\Scripts\python ...` 형태로 실행 |

---

> 이 결과는 참고용이며, 의심되면 결제나 정보 입력 전에 공식 앱이나 기관 대표번호로 확인하세요.
