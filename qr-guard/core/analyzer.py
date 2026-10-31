"""LLM 판정.

LLM 호출은 이 파일 한 곳에만 둔다. 다른 API 로 바꾸려면 _call_llm() 만 고치면 된다.

※ 재현성 메모: 명세는 temperature=0 을 요구했지만, 현재 Claude 모델(Opus 5.5 등)은
  temperature 파라미터를 보내면 400 오류를 낸다. 그래서 temperature 는 보내지 않고,
  (1) 입력 해시별 결과 캐시와 (2) 평가 스크립트의 --repeat 옵션으로 흔들림을 확인한다.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "judge_prompt.md"
CACHE_DIR = ROOT / "cache"

load_dotenv(ROOT / ".env")

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 16000

VERDICTS = {"SAFE", "CAUTION", "DANGER", "UNKNOWN"}
REQUIRED_FIELDS = ["verdict", "evidence", "advice"]
LIST_FIELDS = ["expected_info", "requested_info", "excessive_requests",
               "mismatches", "evidence", "missing_info"]
TEXT_FIELDS = ["notice_purpose", "page_purpose", "advice"]


class LLMError(Exception):
    """사용자에게 그대로 보여줄 수 있는 LLM 오류."""


def unknown_result(reason: str, missing: list | None = None) -> dict:
    """판정 보류(UNKNOWN) 결과를 만든다."""
    return {
        "verdict": "UNKNOWN",
        "notice_purpose": "",
        "page_purpose": "",
        "expected_info": [],
        "requested_info": [],
        "excessive_requests": [],
        "mismatches": [],
        "evidence": [reason],
        "missing_info": missing or [],
        "advice": "판단할 정보가 부족합니다. 결제나 정보 입력 전에 공식 앱이나 기관 대표번호로 확인하세요.",
        "error": reason,
    }


def build_prompt(notice_text: str, url_info: dict, page_info: dict) -> str:
    """프롬프트 파일의 {INPUT_JSON} 자리에 입력을 넣는다."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    payload = {
        "notice_text": notice_text or "(없음)",
        "url_info": url_info,
        "page_info": page_info,
    }
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    # format() 을 쓰면 프롬프트 안의 { } 때문에 깨지므로 replace 를 쓴다
    return template.replace("{INPUT_JSON}", input_json)


def parse_response(text: str) -> dict | None:
    """LLM 응답에서 JSON 을 꺼내 검증한다. 형식이 틀리면 None."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    # 앞뒤에 설명이 붙어도 첫 { 부터 마지막 } 까지만 사용
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if any(f not in data for f in REQUIRED_FIELDS):
        return None
    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        return None
    data["verdict"] = verdict
    # 빠진 필드는 빈 값으로 채워 화면·평가가 깨지지 않게 한다
    for f in LIST_FIELDS:
        v = data.get(f)
        data[f] = v if isinstance(v, list) else ([str(v)] if v else [])
    for f in TEXT_FIELDS:
        data[f] = str(data.get(f) or "")
    data["error"] = None
    return data


def _call_llm(prompt: str, model: str, effort: str) -> str:
    """Anthropic API 를 한 번 호출하고 응답 텍스트를 돌려준다."""
    import anthropic  # API 를 바꿀 때는 이 함수만 고치면 된다

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMError(
            "ANTHROPIC_API_KEY 가 설정되지 않았습니다. "
            ".env.example 을 .env 로 복사하고 API 키를 넣어 주세요."
        )
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": effort},
            # 안전 분류기가 요청을 거절하면 서버가 다른 모델로 자동 재시도
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise LLMError("API 키가 올바르지 않습니다. .env 의 ANTHROPIC_API_KEY 를 확인하세요.")
    except anthropic.NotFoundError:
        raise LLMError(f"모델 '{model}' 을 찾을 수 없습니다. .env 의 LLM_MODEL 을 확인하세요.")
    except anthropic.RateLimitError:
        raise LLMError("API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요.")
    except anthropic.APIConnectionError:
        raise LLMError("Anthropic API 에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
    except anthropic.APIStatusError as e:
        raise LLMError(f"API 오류 ({e.status_code}): {e.message}")

    if response.stop_reason == "refusal":
        raise LLMError("모델이 이 요청에 대한 응답을 거절했습니다.")
    return "".join(b.text for b in response.content if b.type == "text")


def _cache_path(model: str, effort: str, prompt: str) -> Path:
    key = hashlib.sha256(f"{model}|{effort}|{prompt}".encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"{key}.json"


def judge_with_llm(notice_text: str, url_info: dict, page_info: dict,
                   use_cache: bool | None = None) -> dict:
    """안내 문구 + URL 정보 + 페이지 정보로 LLM 판정을 받는다.

    반환: 5장 형식의 dict + "error" (성공이면 None)
    """
    model = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    effort = os.getenv("LLM_EFFORT") or DEFAULT_EFFORT
    if use_cache is None:
        use_cache = os.getenv("LLM_CACHE", "1") == "1"

    try:
        prompt = build_prompt(notice_text, url_info, page_info)
    except Exception as e:
        return unknown_result(f"프롬프트 파일을 읽을 수 없습니다: {e}")

    cache_file = _cache_path(model, effort, prompt)
    if use_cache and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except Exception:
            pass  # 캐시가 깨졌으면 새로 호출

    last_text = ""
    try:
        for _attempt in range(2):  # 형식이 틀리면 한 번 재시도
            last_text = _call_llm(prompt, model, effort)
            data = parse_response(last_text)
            if data is not None:
                data["model"] = model
                if use_cache:
                    CACHE_DIR.mkdir(exist_ok=True)
                    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data
    except LLMError as e:
        return unknown_result(str(e))
    except Exception as e:
        return unknown_result(f"LLM 호출 중 알 수 없는 오류: {e}")

    result = unknown_result("LLM 응답이 정해진 JSON 형식이 아니어서 판정을 보류했습니다.")
    result["raw_response"] = last_text[:2000]
    return result
