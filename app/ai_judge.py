"""AI 페이지 사칭 판별 (요구사항 6-1 ①: HTML 텍스트 분석).

AI에게 '피싱이냐'를 묻지 않는다. 규칙으로 알 수 없는 '의미'만 묻는다.
  - 이 페이지가 어떤 서비스(브랜드)를 표방하는가
  - 페이지의 목적은 무엇인가, 어떤 정보를 입력받는가
  - 그 서비스의 공식 도메인은 무엇이라고 보는가
실제 도메인과의 비교와 점수 반영은 규칙(analyzer)이 한다.

일관성 확보 (2차 멘토링 반영)
  - AI가 검사 항목을 정하지 않는다. 아래 SYSTEM_PROMPT 의 고정 질문 7개와 PageJudgement 고정 JSON 스키마로만 묻는다.
  - temperature 0, seed 고정, 후보 응답 1개 → 같은 입력이면 같은 답이 나오도록
  - 캐시 두 가지
      ① 내용 캐시: 페이지 내용이 같으면 저장한 응답을 재사용 (검증 스크립트를 다시 돌려도 같은 결과)
      ② URL 캐시: 실시간 분석에서 같은 최종 URL은 설정한 시간(ai.url_cache_hours) 동안 같은 결과를 돌려준다.
         페이지에 접속할 때마다 바뀌는 내용(시간·토큰 등) 때문에 판단이 흔들리는 것을 막는다.
"""
import hashlib
import json
import threading
import time
from collections import deque

from pydantic import BaseModel, Field

from . import config

PROMPT_VERSION = 2
INFO_CHOICES = ["아이디/이메일", "비밀번호", "카드번호", "CVC", "카드 유효기간", "계좌번호", "주민등록번호",
                "인증번호/OTP", "휴대폰번호", "이름", "주소", "기타"]

SYSTEM_PROMPT = f"""너는 웹 페이지 분석가다. 주어진 웹 페이지 정보(제목, 본문 일부, 입력란, 이미지 파일명, 링크 도메인)를 보고
이 페이지가 '스스로 어떤 서비스라고 표방하는지'와 '무엇을 하는 페이지인지'를 판단한다.
피싱 여부를 판정하지 말고, 보이는 사실만 정리한다.

출력 규칙
- claimed_service: 페이지가 표방하는 회사·서비스·기관 이름 (예: "국민은행", "네이버", "PayPal", "우체국"). 특정 서비스를 표방하지 않으면 null.
- page_purpose: 페이지 목적을 짧은 한국어로 (예: "인터넷뱅킹 로그인", "택배 배송 조회", "주차요금 결제", "기업 홍보 메인 페이지").
- requested_info: 사용자에게 입력을 요구하는 정보. 다음 중에서만 고른다: {", ".join(INFO_CHOICES)}. 없으면 빈 배열.
- official_domain_guess: claimed_service 의 공식 웹사이트 도메인을 네가 아는 대로 (예: "kbstar.com"). 모르면 null.
- domain_matches_service: 입력의 actual_domain 이 claimed_service 를 운영하는 회사·기관의 도메인으로 보이면 true
  (대표 도메인이 아니어도 그 회사가 운영하는 다른 도메인, 자회사·지역·테스트·데모용 도메인이면 true),
  운영 주체와 관련 없어 보이면 false, 판단할 수 없으면 null.
  도메인 이름에 서비스명이 들어 있다는 이유만으로 true 로 판단하지 않는다. (예: kbstar-login.com, paypal-verify.net 은 false)
- confidence: claimed_service 판단에 대한 확신(0~1).
- reason: 판단 근거를 한국어 한 문장으로.
"""


class PageJudgement(BaseModel):
    claimed_service: str | None = Field(description="페이지가 표방하는 서비스 이름, 없으면 null")
    page_purpose: str = Field(description="페이지 목적")
    requested_info: list[str] = Field(description="입력을 요구하는 정보 종류")
    official_domain_guess: str | None = Field(description="표방 서비스의 공식 도메인 추정")
    domain_matches_service: bool | None = Field(description="실제 도메인이 표방 서비스 운영 주체의 도메인인지")
    confidence: float = Field(description="0~1 확신도")
    reason: str = Field(description="판단 근거 한 문장")


def build_input(page: dict, actual_domain: str) -> dict:
    max_chars = config.settings()["ai"]["max_text_chars"]
    return {
        "actual_domain": actual_domain,
        "title": page["title"],
        "text": page["text"][:max_chars],
        "input_fields": page["requested_info"],
        "form_submit_domains": sorted({f["action_domain"] for f in page["forms"] if f["action_domain"]}),
        "images": page["images"][:15],
        "favicon": page["favicon"],
        "link_domains": page["link_domains"],
    }


class _RateLimiter:
    """무료 한도(분당 요청 수)를 넘지 않게 막는다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: deque[float] = deque()

    def acquire(self, per_minute: int, wait: bool) -> bool:
        while True:
            with self.lock:
                now = time.time()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
                if len(self.calls) < per_minute:
                    self.calls.append(now)
                    return True
                sleep_for = 60 - (now - self.calls[0]) + 0.5
            if not wait:
                return False
            time.sleep(sleep_for)


_limiter = _RateLimiter()
_clients: dict[bool, object] = {}


def _get_client(long_timeout: bool = False):
    """실시간용은 짧은 시간 제한, 검증용(long_timeout)은 긴 시간 제한. SDK 자체 재시도는 끈다."""
    if long_timeout not in _clients:
        from google import genai
        from google.genai import types
        cfg = config.settings()["ai"]
        seconds = cfg["eval_timeout_sec"] if long_timeout else cfg["timeout_sec"]
        _clients[long_timeout] = genai.Client(
            api_key=config.env("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=seconds * 1000, retry_options=types.HttpRetryOptions(attempts=1)))
    return _clients[long_timeout]


def _is_temporary(e: Exception) -> bool:
    """서버 과부하(5xx), 요청 한도(429), 시간 초과처럼 잠시 뒤 다시 하면 될 수 있는 오류인지."""
    from google.genai import errors
    import httpx
    if isinstance(e, errors.ServerError):
        return True
    if isinstance(e, errors.ClientError) and getattr(e, "code", None) == 429:
        return True
    return isinstance(e, (httpx.TimeoutException, TimeoutError))


def _url_cache_path(url: str, model: str):
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, model, url], ensure_ascii=False).encode("utf-8")).hexdigest()
    return config.DATA_DIR / "ai_cache" / "url" / f"{key}.json"


def _read_url_cache(url: str, model: str) -> dict | None:
    hours = config.settings()["ai"].get("url_cache_hours", 0)
    path = _url_cache_path(url, model)
    if hours <= 0 or not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - saved["saved_at"] > hours * 3600:
        return None
    return saved["result"]


def _write_url_cache(url: str, model: str, result: dict) -> None:
    if config.settings()["ai"].get("url_cache_hours", 0) <= 0:
        return
    path = _url_cache_path(url, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "saved_at": time.time(), "result": result}, ensure_ascii=False),
                    encoding="utf-8")


def judge_page(page: dict, actual_domain: str, *, wait: bool = False, url: str | None = None) -> dict:
    """페이지 정보로 AI 판단을 받는다. 실패해도 예외 없이 {"status": "error", "error": ...} 를 돌려준다.

    url 을 주면(실시간 분석) 같은 URL의 최근 결과를 먼저 찾아 쓰고, 새 결과도 URL 기준으로 저장한다.
    """
    cfg = config.settings()["ai"]
    model = cfg["model"]
    if url:
        saved = _read_url_cache(url, model)
        if saved is not None:
            return {"status": "ok", "model": model, "cached": True, "result": saved}
        res = judge_page(page, actual_domain, wait=wait)
        if res["status"] == "ok":
            _write_url_cache(url, model, res["result"])
        return res

    payload = build_input(page, actual_domain)
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, model, payload], ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()
    cache = config.DATA_DIR / "ai_cache" / f"{key}.json"
    if cache.exists():
        return {"status": "ok", "model": model, "cached": True, "result": json.loads(cache.read_text(encoding="utf-8"))}

    if not config.env("GEMINI_API_KEY"):
        return {"status": "error", "model": model, "error": "Gemini API 키 없음"}
    if not _limiter.acquire(cfg["max_requests_per_minute"], wait):
        return {"status": "error", "model": model, "error": "AI 요청 한도 초과(잠시 후 다시 시도)"}

    from google.genai import types
    # 실시간 분석(wait=False)은 한 번만 시도한다. 검증(wait=True)은 서버 과부하·시간 초과면 기다렸다 다시 시도한다.
    delays = cfg["eval_retry_delays_sec"] if wait else []
    for attempt in range(len(delays) + 1):
        try:
            resp = _get_client(long_timeout=wait).models.generate_content(
                model=model,
                contents="다음 웹 페이지 정보를 분석해 줘.\n" + json.dumps(payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=PageJudgement,
                    temperature=0,
                    seed=cfg.get("seed", 0),
                    candidate_count=1,
                ),
            )
            result = PageJudgement.model_validate_json(resp.text).model_dump()
            break
        except Exception as e:  # noqa: BLE001 - 시간 초과·한도·형식 오류 모두 'AI 분석 불가'
            if attempt < len(delays) and _is_temporary(e):
                time.sleep(delays[attempt])
                _limiter.acquire(cfg["max_requests_per_minute"], wait=True)
                continue
            return {"status": "error", "model": model, "error": f"AI 분석 불가 ({type(e).__name__})"}

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "model": model, "cached": False, "result": result}
