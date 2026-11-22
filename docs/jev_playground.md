# Jev Playground 테스트 시트

TypeSafe 콘솔(<https://console.typesafe.ai>)의 **Playground**에서 질문 문구와 한국어 처리를 직접 확인하기 위한 시트입니다.
같은 내용을 스크립트로 자동 비교한 결과는 `eval/jev_prompt_results.md`(문구 비교), `eval/jev_typo_results.md`(오타 내성)에 있습니다.

## 사용법
1. 콘솔 로그인 → Playground → 모델 `jev-latest`
2. 아래 **질문(Question)** 하나를 고르고, **상황(State)** 을 바꿔 가며 붙여 넣기
3. 결과(선택, 확률)를 맨 아래 기록표에 적기

## 질문 후보

### V3 — 영어 지시 + 한국어 데이터 (자동 테스트 1위, 서비스 적용 예정)
- 유형: Choice
- 지시(Instructions):
```
Decide whether the purpose stated on the QR notice matches the purpose and requested information of the web page opened by scanning the QR. Treat as a mismatch: login to a different service than the notice, requests for sensitive information unnecessary for the notice's purpose (resident registration number, passwords, card CVC, account PIN, etc.), or requests to install an app file (apk) directly.
```
- 선택지(Criteria):
  - `일치`: The page does what the notice says and asks only for information needed for it
  - `불일치`: The page does something else, or asks for sensitive info or app installs unnecessary for the notice's purpose
  - `판단불가`: Not enough information to decide

### V1 — 상세 한국어 (비교용)
- 유형: Choice
- 지시:
```
QR 안내문이 안내한 목적과, QR을 찍고 접속한 웹 페이지의 목적·요구 정보가 일치하는지 판단한다. 안내문과 다른 서비스의 로그인, 안내 목적에 필요 없는 민감 정보(주민등록번호, 비밀번호, 카드 CVC, 계좌 비밀번호 등) 요구, 앱 파일(apk) 직접 설치 요구는 불일치로 본다.
```
- 선택지: `일치`: 페이지가 안내문이 말한 일을 하고, 그 일에 필요한 정보만 요구한다 / `불일치`: 페이지가 안내문과 다른 일을 하거나, 안내 목적에 필요 없는 민감 정보·앱 설치를 요구한다 / `판단불가`: 정보가 부족해 판단할 수 없다

### V2 — 짧은 한국어 (비교용, 자동 테스트에서 가장 약함)
- 지시: `안내문 목적과 접속한 페이지 목적이 일치하는가?` / 선택지: `일치`, `불일치`, `판단불가` (설명 없음)

## 상황(State) 예시

**S1. 정상 (기대: 일치)**
```json
{"QR 안내문": "공영주차장 요금 정산 안내\n한빛시설관리공단 제3공영주차장\n출차 전 아래 QR코드를 스캔하여\n주차요금을 결제해 주세요.",
 "접속한 페이지": {"제목": "제3공영주차장 요금 정산", "표방 서비스": "한빛시설관리공단", "페이지 목적": "차량번호 입력 후 주차요금 카드 결제", "입력 요구 정보": ["차량번호", "카드번호", "카드 유효기간"]}}
```

**S2. 엉뚱한 페이지 (기대: 불일치)** — 시연 사례 1과 같은 상황
```json
{"QR 안내문": "공영주차장 요금 정산 안내\n한빛시설관리공단 제3공영주차장\n출차 전 아래 QR코드를 스캔하여\n주차요금을 결제해 주세요.",
 "접속한 페이지": {"제목": "가을맞이 경품 이벤트", "표방 서비스": null, "페이지 목적": "경품 이벤트 응모", "입력 요구 정보": ["이름", "휴대폰번호", "주소"]}}
```

**S3. 애매한 경우 (기대: 일치, 자동 테스트에서 V1·V2가 틀림)**
```json
{"QR 안내문": "공영주차장 요금 정산 안내\n한빛시설관리공단 제3공영주차장\n출차 전 아래 QR코드를 스캔하여\n주차요금을 결제해 주세요.",
 "접속한 페이지": {"제목": "한빛파킹 앱으로 정산하기", "표방 서비스": "한빛시설관리공단", "페이지 목적": "주차 정산 앱 설치 안내(구글 플레이·앱스토어 공식 링크)", "입력 요구 정보": ["없음"]}}
```

**S4. 교묘한 경우 (기대: 불일치)** — 목적은 같은데 필요 없는 민감 정보 요구
```json
{"QR 안내문": "공영주차장 요금 정산 안내\n한빛시설관리공단 제3공영주차장\n출차 전 아래 QR코드를 스캔하여\n주차요금을 결제해 주세요.",
 "접속한 페이지": {"제목": "제3공영주차장 요금 정산", "표방 서비스": "한빛시설관리공단", "페이지 목적": "주차요금 결제", "입력 요구 정보": ["차량번호", "카드번호", "CVC", "주민등록번호", "비밀번호"]}}
```

**S5. 안내문 읽기 오타 30% (기대: S2와 같은 불일치)** — 오타가 섞여도 판정이 유지되는지
```json
{"QR 안내문": "공영주차장요금 정사 안내 한빛시설관리고단 제3공여주차장\n출차 전 아레QR코드를스켄어 주차요금을 걸재헤주세요,",
 "접속한 페이지": {"제목": "가을맞이 경품 이벤트", "표방 서비스": null, "페이지 목적": "경품 이벤트 응모", "입력 요구 정보": ["이름", "휴대폰번호", "주소"]}}
```

## 기록표

| 날짜 | 질문 | 상황 | 선택 | 불일치 확률 | 기대와 같은가 | 메모 |
|---|---|---|---|---|---|---|
| | V3 | S1 | | | | |
| | V3 | S2 | | | | |
| | V3 | S3 | | | | |
| | V3 | S4 | | | | |
| | V3 | S5 | | | | |
| | V1 | S3 | | | | |
| | V2 | S3 | | | | |

> 확인할 점: 한국어 데이터를 제대로 이해하는가, 질문 문구에 따라 애매한 경우(S3)의 판정이 바뀌는가, 오타(S5)에도 판정이 유지되는가.
