# qr-guard · QR 피싱 판별 서비스

QR 코드를 찍기 전에, **QR 옆 안내 문구**와 **실제 연결 페이지**를 비교해 피싱 위험을 근거와 함께 판정하는 웹 데모입니다.
비교용으로 "URL만 검사하는" 규칙 판정기(baseline)도 함께 보여줍니다.

판정 등급: **안전(SAFE) / 주의(CAUTION) / 위험(DANGER) / 판단 보류(UNKNOWN)**

> 👉 **처음 실행한다면 [GETTING_STARTED.md](GETTING_STARTED.md)를 순서대로 따라 하세요.** (프로그램 설치부터 VS Code 실행까지)

## 빠른 시작 (윈도우)

Python 3.10~3.12가 필요합니다. 3.13 이상에서는 opencv 설치가 안 될 수 있습니다.

```powershell
# 처음 한 번만: 설치
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # .env 를 열어 ANTHROPIC_API_KEY 를 채우세요 (선택). 절대 커밋 금지.

# 실행: 터미널 두 개
.venv\Scripts\python mock_site\server.py      # 터미널 1: 가짜 테스트 페이지 서버 (http://127.0.0.1:5001)
.venv\Scripts\streamlit run app.py            # 터미널 2: 데모 화면 (http://localhost:8501)
```

시연할 때는 화면의 **"테스트 시나리오 불러오기"**에서 `P1`을 고르고 **검사하기**를 누르세요.
`data/qr_samples/P1.png`를 "QR 이미지 업로드" 탭에 올려도 됩니다. 이때도 시나리오를 먼저 골라 두어야 테스트 모드로 동작합니다.

## 커맨드라인 도구

```bash
python core/pipeline.py P1            # 시나리오 하나 판정 (JSON 출력)
python core/pipeline.py P1 --no-llm   # LLM 없이 (API 키 불필요)
python core/pipeline.py P1 --full     # 추출 정보까지 전부 출력

python eval/run_eval.py               # 정답셋 전체로 baseline vs 우리 방식 비교
python eval/run_eval.py --repeat 3    # 같은 사례 3번 → 판정이 흔들리는 사례 표시
python eval/run_eval.py --no-cache    # LLM 캐시 무시

python data/make_qr_samples.py        # 시나리오별 QR 이미지 다시 만들기
pytest                                # 테스트 (서버가 켜져 있으면 시나리오 테스트까지 실행)
```

**자세한 사용 방법은 [USAGE.md](USAGE.md)를 보세요.**

평가 결과는 `eval/results/<날짜_시간>/`에 `cases.csv`, `cases.md`, `summary.md`, `diff.md`로 저장됩니다.

## 폴더 구조

```
app.py                 Streamlit 화면
core/
  qr_decode.py         QR 이미지 → URL (opencv)
  url_trace.py         리다이렉트 추적 (HTTP 3xx, meta refresh, location.href)
  url_features.py      URL 특징 추출, 유사 도메인 탐지
  page_extract.py      페이지 제목·본문·입력란·요구 정보·앱 설치·압박 문구 추출
  baseline.py          URL만 보는 점수 규칙 판정 (점수는 파일 맨 위 상수)
  analyzer.py          LLM 판정 (LLM 호출은 이 파일에만 있음)
  pipeline.py          전체 연결: analyze() 하나로 화면·평가가 모두 동작
prompts/judge_prompt.md  LLM 판정 프롬프트 (팀이 고쳐 가며 성능 개선)
mock_site/             가짜 페이지 서버와 HTML
data/scenarios.json    정답셋 (현재 예시 8개, 팀이 15~20개로 확장)
data/brands.json       유명 브랜드·기관 도메인 목록
data/qr_samples/       시나리오별 예시 QR 이미지
eval/run_eval.py       비교 평가
tests/                 pytest
```

## 처리 흐름

1. 입력: QR 이미지가 있으면 해독하고, 없으면 입력한 URL을 씁니다.
2. 리다이렉트 추적: 최대 10번, 타임아웃 5초, 본문은 최대 1MB까지만 읽습니다.
3. URL 특징 추출 → 4. baseline 판정
5. 페이지 추출: 입력란을 카드번호, CVC, 주민번호, 계좌 같은 범주로 분류합니다.
6. LLM 판정: 안내 목적에 비해 과도한 요구가 있는지, 불일치가 있는지 판단합니다.
7. 후처리: 페이지를 못 불러왔거나 LLM 응답 형식이 틀리면 UNKNOWN으로 둡니다. APK 다운로드로 이어지면 최소 CAUTION입니다.

## 팀이 알아둘 점

- **temperature**: 명세는 temperature=0을 요구하지만, 현재 Claude 모델(`claude-opus-5-5` 등)은 temperature 값을 보내면 오류가 납니다. 그래서 보내지 않습니다. 대신 같은 입력의 결과를 `cache/`에 저장해 재사용하고(`LLM_CACHE=1`), `--repeat 3`으로 판정이 흔들리는지 확인합니다.
- **모델·비용**: `.env`의 `LLM_MODEL`, `LLM_EFFORT`로 바꿀 수 있습니다. 비용을 줄이려면 `LLM_EFFORT=low`부터 시도해 보세요. 안전 분류기가 요청을 거절하면 서버가 다른 모델로 자동 재시도하도록 설정되어 있습니다(`analyzer.py`의 `fallbacks`).
- **테스트 모드**: 시나리오의 `override` 표(`표시용 URL → 로컬 URL`)에 없는 주소에는 접속하지 않습니다. 실제 피싱 사이트에 접속하는 일이 없습니다.
- **정답셋 추가**: `data/scenarios.json`에 항목을 추가하고, `mock_site/pages/`에 HTML을, `mock_site/server.py`에 경로를 추가하세요. 정상·피싱·애매를 비슷한 비율로 맞추고, baseline이 맞히는 사례도 꼭 넣으세요.
- **baseline 공정성**: baseline을 일부러 약하게 만들지 마세요. 점수는 `core/baseline.py` 맨 위 상수에 있습니다.

## 안전 규칙

- 개발·평가·시연은 가짜 페이지로만 합니다.
- 실제 URL을 검사할 때도 GET 요청만 보냅니다. form 제출, 스크립트 실행, 파일 다운로드는 하지 않습니다.
- 가짜 서버는 127.0.0.1에서만 열리고, 입력값을 저장하거나 전송하지 않습니다.
- `.env`와 `cache/`는 `.gitignore`에 들어 있습니다.

> 이 결과는 참고용이며, 의심되면 결제나 정보 입력 전에 공식 앱이나 기관 대표번호로 확인하세요.
