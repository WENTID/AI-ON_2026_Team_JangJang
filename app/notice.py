"""QR 안내문 읽기 (2단계: Gemini OCR).

카메라 프레임·업로드 사진에서 QR 주변 안내문을 Gemini로 읽어
글자 전부, 안내 목적, 기관명, 결제 요구, 덧붙인 스티커 의심 여부를 고정 JSON 형식으로 받는다.

- 사진은 긴 변 settings.notice.max_side px로 줄여 보낸다. (토큰·시간 절약)
- 사진이 올라오는 순간(/api/image) prefetch() 로 미리 읽기 시작해, 분석 요청 때는 결과만 가져다 쓴다.
- 같은 사진은 캐시(data/ai_cache/notice/)를 재사용한다.
- 실패해도 예외를 던지지 않는다. (맥락 비교만 '확인 불가'가 되고 나머지 분석은 계속)
검증: scripts/ocr_test.py (합성 안내문 77장, eval/ocr_results.md)
"""
import hashlib
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum
from functools import lru_cache

import cv2
from pydantic import BaseModel, Field

from . import ai_judge, config
from .uploads import image_path

PROMPT_VERSION = 1
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notice")
_jobs: dict[str, Future] = {}
_lock = threading.Lock()


def _prompt() -> str:
    purposes = ", ".join(config.settings()["notice"]["purposes"])
    return f"""너는 QR코드가 붙은 안내문 사진을 읽는 분석가다. 사진에 보이는 사실만 답한다.
- full_text: 사진 속 글자를 위에서 아래로 빠짐없이, 보이는 그대로 적는다. 줄은 줄바꿈으로 구분. 추측해서 고치지 않는다. 읽을 수 없는 글자는 생략.
- purpose: 이 안내문이 QR을 찍게 하는 목적. 다음 중 하나: {purposes}
- organization: 안내문을 붙인 기관·가게 이름 (보이는 그대로). 없으면 null.
- requests_payment: QR을 찍은 뒤 돈을 내도록(결제·납부) 안내하면 true.
- sticker_suspected: QR코드가 원래 인쇄된 것이 아니라 위에 덧붙인 스티커로 보이면 true (테두리·그림자·비뚤어짐·종이 질감 차이).
- readable: 글자를 대부분 읽을 수 있으면 true. 안내문이 없거나 QR만 찍힌 사진이면 false.
"""


@lru_cache
def _schema():
    Purpose = Enum("Purpose", {p: p for p in config.settings()["notice"]["purposes"]}, type=str)

    class NoticeResult(BaseModel):
        full_text: str = Field(description="사진 속 글자 전부")
        purpose: Purpose = Field(description="QR 안내 목적")
        organization: str | None = Field(description="기관·가게 이름")
        requests_payment: bool = Field(description="결제·납부 요구 여부")
        sticker_suspected: bool = Field(description="QR 위에 스티커를 덧붙인 흔적")
        readable: bool = Field(description="안내문 글자를 대부분 읽을 수 있는지")

    return NoticeResult


def _shrink(path) -> bytes | None:
    """긴 변을 max_side px 이하로 줄여 JPEG 바이트로. 작으면 그대로."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    max_side = config.settings()["notice"]["max_side"]
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None


def _read(image_id: str) -> dict:
    cfg = config.settings()
    if not cfg["notice"]["enabled"] or not cfg["ai"]["enabled"]:
        return {"status": "error", "error": "안내문 읽기 꺼짐"}
    path = image_path(image_id)
    if path is None:
        return {"status": "error", "error": "사진 없음"}
    data = _shrink(path)
    if data is None:
        return {"status": "error", "error": "사진을 읽을 수 없음"}
    model = cfg["ai"]["model"]
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, model, cfg["notice"]["max_side"]]).encode() + data).hexdigest()
    cache = config.DATA_DIR / "ai_cache" / "notice" / f"{key}.json"
    if cache.exists():
        return {**json.loads(cache.read_text(encoding="utf-8")), "cached": True}
    if not config.env("GEMINI_API_KEY"):
        return {"status": "error", "error": "Gemini API 키 없음"}
    # 백그라운드 작업이라 요청 한도에 걸리면 잠시 기다린다. (분석 쪽은 read_notice 의 timeout_sec 까지만 기다림)
    ai_judge._limiter.acquire(cfg["ai"]["max_requests_per_minute"], wait=True)

    from google.genai import types
    schema = _schema()
    start = time.perf_counter()
    try:
        resp = ai_judge._get_client().models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), "이 안내문 사진을 분석해 줘."],
            config=types.GenerateContentConfig(
                system_instruction=_prompt(), response_mime_type="application/json", response_schema=schema,
                temperature=0, seed=cfg["ai"].get("seed", 0), candidate_count=1),
        )
        result = schema.model_validate_json(resp.text).model_dump(mode="json")
    except Exception as e:  # noqa: BLE001 - 시간 초과·한도·형식 오류 모두 '안내문 읽기 실패'
        return {"status": "error", "error": f"안내문 읽기 실패 ({type(e).__name__})"}
    out = {"status": "ok", "model": model, "seconds": round(time.perf_counter() - start, 2), "result": result}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return {**out, "cached": False}


def prefetch(image_id: str) -> None:
    """사진이 올라오자마자 백그라운드에서 읽기 시작한다."""
    with _lock:
        if image_id not in _jobs:
            _jobs[image_id] = _pool.submit(_read, image_id)
            if len(_jobs) > 200:  # 오래된 작업 기록 정리
                for k in list(_jobs)[:100]:
                    if _jobs[k].done():
                        del _jobs[k]


def read_notice(image_id: str | None) -> dict:
    """안내문 읽기 결과. 미리 시작한 작업이 있으면 기다렸다 받고, 없으면 지금 읽는다."""
    if not image_id or image_path(image_id) is None:
        return {"status": "none", "error": "사진 없음"}
    prefetch(image_id)
    try:
        return _jobs[image_id].result(timeout=config.settings()["notice"]["timeout_sec"])
    except Exception as e:  # noqa: BLE001 - 시간 초과 등
        return {"status": "error", "error": f"안내문 읽기 실패 ({type(e).__name__})"}
