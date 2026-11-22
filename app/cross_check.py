"""QR 안내문 ↔ 접속 페이지 교차 분석 (2단계 맥락 분석의 2차).

1차(context.py)는 안내문을 정해진 항목(목적·기관·결제·스티커)으로 줄여 Jev가 '목적이 맞는지'만 판정한다.
2차는 Gemini 멀티모달이 안내문 원본 사진과 접속 페이지를 함께 보고, 1차·정책 검사에서 이미 검사한 항목을
제외한 '추가 문제점'을 자유롭게 찾는다. (예: 안내문 금액과 페이지 금액이 다름, 안내문 기관과 페이지 사업자가 다름)

무엇을 찾을지는 AI에게 맡기되, 하네스는 지킨다.
  - 살펴볼 관점은 설정(settings.cross_check.perspectives)으로 힌트만 준다. 힌트에 없는 것은 '기타'로.
  - 답하는 형식은 고정 JSON 스키마(관점·설명·안내문 근거·페이지 근거·심각도).
  - 환각 방지: AI가 인용한 근거 문장이 안내문(1차에서 읽은 글자)이나 페이지 본문에 실제로 있는지 프로그램이
    확인하고, 확인되지 않으면 버린다.
  - temperature 0·seed 고정·결과 캐시.
점수 반영 방식은 settings.cross_check.scoring 으로 바꾼다. (멘토링 피드백 후 확정 예정)
  display_only       : 화면에 표시만, 점수 없음
  weak_capped        : 약한 신호로 반영, 합계 상한 max_points
  with_other_signals : 다른 위험 신호가 이미 있을 때만 weak_capped 처럼 반영
실패해도 예외를 던지지 않는다. (나머지 분석은 그대로)
"""
import hashlib
import json
import re
from difflib import SequenceMatcher
from enum import Enum
from functools import lru_cache

from pydantic import BaseModel, Field

from . import ai_judge, config, notice
from .uploads import image_path

PROMPT_VERSION = 2
SEVERITIES = ["낮음", "중간", "높음"]


def _cfg() -> dict:
    return config.settings()["cross_check"]


def _prompt(already_checked: list[str]) -> str:
    c = _cfg()
    hints = "\n".join(f"- {p['name']}: {p['hint']}" for p in c["perspectives"])
    checked = "\n".join(f"- {x}" for x in already_checked)
    return f"""너는 QR코드 안내문과, 그 QR을 찍으면 열리는 웹 페이지를 함께 비교하는 보안 분석가다.
사람이 정한 검사 규칙이 이미 아래 항목을 검사했다. 이 항목들은 다시 보고하지 않는다.
[이미 검사한 항목]
{checked}
특히 '안내문의 목적과 페이지의 목적이 다르다', '안내문과 무관한 개인정보·민감 정보를 요구한다'는 이미 판정했다.
이것을 다른 말(유도 행위, 무관한 페이지 등)로 바꿔 다시 보고하지 않는다. 목적이 서로 다르더라도, 그 차이 말고
새로 드러나는 구체적인 사실(금액·번호·날짜·기관명·문구 등)만 보고한다.

그 밖에 사람이 놓치기 쉬운, 안내문과 페이지 사이의 어긋남이나 수상한 점을 찾는다.
살펴볼 관점(힌트일 뿐, 여기에 없는 문제는 '기타'로 보고):
{hints}

규칙
- 근거가 있는 것만 보고한다. notice_evidence 는 사진 속 안내문 글자를, page_evidence 는 페이지 제목·본문 글자를 고치지 말고 그대로 짧게(40자 이내) 인용한다. 해당 쪽에 근거가 없으면 빈 문자열.
- 둘 중 적어도 하나는 인용해야 한다. 인용할 수 없는 추측은 보고하지 않는다.
- 정상적인 차이(같은 기관의 다른 표기, 페이지가 안내문보다 자세한 것)는 문제로 보지 않는다.
- 문제가 없으면 findings 를 빈 배열로 둔다. 최대 {c['max_findings']}개, 중요한 것부터.
- severity: 높음(돈·개인정보를 잃을 수 있는 어긋남) / 중간(의심스럽지만 단정하기 어려움) / 낮음(사소한 이상).
"""


@lru_cache
def _schema():
    Perspective = Enum("Perspective", {p["name"]: p["name"] for p in _cfg()["perspectives"]} | {"기타": "기타"},
                       type=str)
    Severity = Enum("Severity", {s: s for s in SEVERITIES}, type=str)

    class Finding(BaseModel):
        perspective: Perspective = Field(description="관점")
        description: str = Field(description="발견한 문제를 한국어 한 문장으로")
        notice_evidence: str = Field(description="안내문에서 그대로 인용한 근거, 없으면 빈 문자열")
        page_evidence: str = Field(description="페이지에서 그대로 인용한 근거, 없으면 빈 문자열")
        severity: Severity = Field(description="낮음 / 중간 / 높음")

    class CrossResult(BaseModel):
        findings: list[Finding] = Field(description="추가로 발견한 문제점")

    return CrossResult


def _norm(s: str) -> str:
    """비교용: 공백·문장부호를 없애고 소문자로."""
    return re.sub(r"[\s\W_]+", "", s or "").lower()


def quote_found(quote: str, source: str) -> bool:
    """인용문이 원문에 있는지. 사진을 다시 읽으며 생기는 작은 글자 차이는 허용한다(가장 긴 일치가 85% 이상)."""
    q, s = _norm(quote), _norm(source)
    if len(q) < 2:
        return False
    if q in s:
        return True
    m = SequenceMatcher(None, s, q, autojunk=False).find_longest_match(0, len(s), 0, len(q))
    return m.size >= 0.85 * len(q)


def verify(findings: list[dict], notice_text: str, page_text: str) -> tuple[list[dict], list[dict]]:
    """근거 확인: 인용한 근거가 모두 원문에 있어야 통과. 반환 (통과, 제외)"""
    kept, dropped = [], []
    for f in findings:
        ne, pe = f["notice_evidence"].strip(), f["page_evidence"].strip()
        ok = bool(ne or pe) and (not ne or quote_found(ne, notice_text)) and (not pe or quote_found(pe, page_text))
        (kept if ok else dropped).append(f)
    return kept, dropped


def page_text_of(page: dict | None) -> str:
    if not page:
        return ""
    return f"{page.get('title') or ''}\n{(page.get('text') or '')[:_cfg()['page_text_chars']]}"


def _ask(image_id: str, notice_text: str, page_info: dict, already_checked: list[str]) -> dict:
    data = notice._shrink(image_path(image_id))
    if data is None:
        return {"status": "error", "error": "사진을 읽을 수 없음"}
    ai = config.settings()["ai"]
    payload = {"QR 안내문 글자(1차에서 읽은 것)": notice_text, "접속한 페이지": page_info}
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, ai["model"], _cfg()["perspectives"], already_checked, payload],
                                  ensure_ascii=False, sort_keys=True).encode() + data).hexdigest()
    cache = config.DATA_DIR / "ai_cache" / "cross_check" / f"{key}.json"
    if cache.exists():
        return {**json.loads(cache.read_text(encoding="utf-8")), "cached": True}
    if not config.env("GEMINI_API_KEY"):
        return {"status": "error", "error": "Gemini API 키 없음"}
    # 실시간 분석이라 한도에 걸리면 기다리지 않고 생략한다.
    if not ai_judge._limiter.acquire(ai["max_requests_per_minute"], wait=False):
        return {"status": "error", "error": "AI 요청 한도에 걸려 추가 분석 생략"}
    from google.genai import types
    import time
    schema = _schema()
    for attempt in range(2):  # 서버 과부하(5xx)·시간 초과 같은 일시 오류는 한 번 더 시도
        try:
            resp = ai_judge._get_client().models.generate_content(
                model=ai["model"],
                contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                          json.dumps(payload, ensure_ascii=False)],
                config=types.GenerateContentConfig(
                    system_instruction=_prompt(already_checked), response_mime_type="application/json",
                    response_schema=schema, temperature=0, seed=ai.get("seed", 0), candidate_count=1))
            result = schema.model_validate_json(resp.text).model_dump(mode="json")
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 0 and ai_judge._is_temporary(e):
                time.sleep(2)
                continue
            return {"status": "error", "error": f"추가 분석 실패 ({type(e).__name__})"}
    out = {"status": "ok", "findings": result["findings"][: _cfg()["max_findings"]]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return {**out, "cached": False}


def score_signal(findings: list[dict], other_risk: bool) -> dict | None:
    """점수 반영 방식(scoring)에 따라 신호 하나로 묶는다. 반영하지 않으면 None."""
    c = _cfg()
    mode = c["scoring"]
    if mode == "display_only" or not findings or (mode == "with_other_signals" and not other_risk):
        return None
    points = min(sum(c["points"].get(f["severity"], 0) for f in findings), c["max_points"])
    if points <= 0:
        return None
    spec = config.scoring()["signals"]["ai_cross_finding"]
    detail = " / ".join(f"[{f['perspective']}] {f['description']}" for f in findings)
    return {"id": "ai_cross_finding", "tier": spec["tier"], "points": points, "label": spec["label"],
            "detail": detail, "source": "AI 판단"}


def _empty(status: str, message: str) -> dict:
    return {"status": status, "message": message, "findings": [], "dropped": 0,
            "scoring": _cfg()["scoring"], "signal": None}


def analyze_cross(image_id: str | None, context: dict, *, page: dict | None, ai_result: dict | None,
                  final_domain: str, signals: list[dict], use_ai: bool = True) -> dict:
    """1차 맥락 분석이 끝난 뒤 호출. 반환: status(ok/skipped/error), message, findings, dropped(근거 확인 실패 수),
    scoring(점수 반영 방식), signal(점수에 넣을 신호 또는 None)"""
    c = _cfg()
    if not use_ai or not c["enabled"] or not config.settings()["ai"]["enabled"]:
        return _empty("skipped", "추가 분석 꺼짐")
    if context.get("status") != "ok" or not context.get("notice"):
        return _empty("skipped", "안내문과 페이지를 함께 비교한 경우에만 추가 분석")
    page_text = page_text_of(page)
    if not page_text.strip():
        return _empty("skipped", "페이지 내용이 없어 추가 분석 생략")

    # 이미 검사한 항목: 정책 검사·1차 맥락 검사의 검사 항목 이름 + 실제로 걸린 신호
    labels = [v["label"] for k, v in config.scoring()["signals"].items()
              if v["tier"] not in ("safe", "fact") and k != "ai_cross_finding"]
    found = [f"{s['label']}: {s['detail']}" for s in signals if s["tier"] != "safe"]
    already = labels + [f"(이번 검사에서 발견됨) {x}" for x in found]
    page_info = {"도메인": final_domain, "제목": (page or {}).get("title") or "",
                 "본문 일부": (page or {}).get("text", "")[: c["page_text_chars"]],
                 "입력 요구 정보": (ai_result or {}).get("requested_info") or (page or {}).get("requested_info") or []}

    res = _ask(image_id, context["notice"]["text"], page_info, already)
    if res["status"] != "ok":
        return _empty("error", res["error"])
    kept, dropped = verify(res["findings"], context["notice"]["text"], page_text)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    kept.sort(key=lambda f: order[f["severity"]])
    other_risk = any(s["tier"] == "confirmed" or (s["tier"] in ("strong", "weak") and s["points"] > 0) for s in signals)
    out = _empty("ok", f"추가로 발견한 점 {len(kept)}건" if kept else "추가로 발견한 문제 없음")
    out.update(findings=kept, dropped=len(dropped), signal=score_signal(kept, other_risk))
    return out
