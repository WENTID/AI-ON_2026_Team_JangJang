"""Jev(TypeSafe) 호출 모듈. 맥락 일치 판정에 쓴다.

Jev는 글을 생성하지 않고 '질문 + 선택지'에 확률로 답하는 판단 모델이다.
(예: 일치 0.02 / 불일치 0.97 / 판단불가 0.01) → 확률을 설정 파일의 기준값과 비교해 점수 신호로 만든다.

- 공식 SDK(typesafe-sdk)를 쓴다. API 키는 .env 의 TYPESAFE_API_KEY.
- 요금: 입력 토큰만 과금(100만 토큰당 $0.042), 출력 무료.
- 실패해도 예외를 던지지 않고 {"status": "error", ...} 를 돌려준다. (외부 API가 실패해도 나머지 분석은 계속)
"""
import hashlib
import json
import time

from . import config

_client = None


def available() -> bool:
    return bool(config.settings().get("jev", {}).get("enabled", True) and config.env("TYPESAFE_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        import typesafe_sdk as ts
        cfg = config.settings()["jev"]
        _client = ts.TypeSafeClient(
            api_key=config.env("TYPESAFE_API_KEY"), model=cfg["model"], timeout=cfg["timeout_sec"],
            retry=ts.RetryPolicy(max_retries=cfg.get("max_retries", 1), timeout=cfg["timeout_sec"]))
    return _client


def ask(state, questions: dict, *, images: dict[str, bytes] | None = None, cache: bool = True) -> dict:
    """Jev에 한 번 묻는다.

    state: 판단할 내용(문자열 또는 JSON). questions: {"이름": {"type": "choice"|"noul", ...}}
    images: {"참조 문자열": 이미지 바이트} (state·질문 안에 참조 문자열을 써서 가리킨다. 실험 기능)
    반환: {"status": "ok", "answers": {...}, "input_tokens": n, "seconds": s, "model": ...}
          또는 {"status": "error", "error": "..."}
    """
    if not available():
        return {"status": "error", "error": "Jev API 키 없음"}
    cfg = config.settings()["jev"]
    key_src = [cfg["model"], state, questions,
               {k: hashlib.sha1(v).hexdigest() for k, v in (images or {}).items()}]
    key = hashlib.sha1(json.dumps(key_src, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    path = config.DATA_DIR / "ai_cache" / "jev" / f"{key}.json"
    if cache and path.exists():
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}

    import typesafe_sdk as ts
    kwargs = {}
    if images:
        kwargs["assets"] = {k: ts.Image(v) for k, v in images.items()}
        kwargs["extra_headers"] = {"TypeSafe-Danger-Zone-Images": "true"}
    start = time.perf_counter()
    try:
        resp = _get_client().system_one(state=state, questions=questions, **kwargs)
    except Exception as e:  # noqa: BLE001 - 인증·한도·시간 초과·형식 오류 모두 'Jev 분석 불가'
        return {"status": "error", "error": f"Jev 분석 불가 ({type(e).__name__})"}
    out = {"status": "ok", "model": resp.model, "seconds": round(time.perf_counter() - start, 2),
           "answers": {k: v.model_dump(mode="json") for k, v in resp.answers.items()},
           "input_tokens": resp.usage.input_tokens if resp.usage else 0}
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return {**out, "cached": False}
