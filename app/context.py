"""QR 안내문 맥락 일치 분석 (요구사항 6-2, 2단계).

흐름
  1. 안내문 읽기: notice.read_notice() — Gemini가 카메라 프레임·업로드 사진에서 안내문 글자·목적을 읽음
  2. 페이지 요약: AI 사칭 판별 결과(페이지 목적·표방 서비스·요구 정보)가 있으면 그것을, 없으면 페이지 제목·본문 일부를 씀
  3. 맥락 판정: Jev에 '안내문 목적과 페이지 목적·요구 정보가 일치하는가'를 묻고 불일치 확률을 받음
     - 불일치 확률 ≥ context.mismatch_strong → ai_context_mismatch (강한 신호)
     - 불일치 확률 ≥ context.mismatch_check  → ai_context_unclear (약한 신호, 확인 권장)
     - Jev가 실패하면 Gemini로 대신 판정 (확률이 없으므로 '불일치'면 강한 신호)
  4. 덧붙인 스티커: 안내문 읽기에서 스티커 의심이면 qr_sticker (약한 신호)
AI가 '피싱이냐'를 판정하지 않는다. 목적이 맞는지만 묻고, 점수 반영은 scoring.json 이 한다.
질문 문구 선택 근거: eval/jev_prompt_results.md (V3: 영어 지시 + 한국어 데이터), 오타 내성: eval/jev_typo_results.md
"""
import hashlib
import json
from enum import Enum

from pydantic import BaseModel, Field

from . import ai_judge, config, jev, notice
from .scoring import make_signal
from .uploads import UPLOAD_DIR, image_path, load_meta  # noqa: F401  (main.py 가 UPLOAD_DIR 을 여기서 가져다 씀)

QUESTION = {
    "type": "choice",
    "instructions": (
        "Decide whether the purpose stated on the QR notice matches the purpose and requested information of the web "
        "page opened by scanning the QR. Treat as a mismatch: login to a different service than the notice, requests "
        "for sensitive information unnecessary for the notice's purpose (resident registration number, passwords, card "
        "CVC, account PIN, etc.), or requests to install an app file (apk) directly."),
    "criteria": {
        "일치": "The page does what the notice says and asks only for information needed for it",
        "불일치": "The page does something else, or asks for sensitive info or app installs unnecessary for the notice's purpose",
        "판단불가": "Not enough information to decide",
    },
}


def page_summary(page: dict | None, ai_result: dict | None, download_name: str | None) -> dict | None:
    """Jev에 넣을 '접속한 페이지' 요약. AI 판별 결과가 있으면 그 형식, 없으면 페이지 원문 일부."""
    out: dict = {}
    if ai_result:
        out = {"제목": (page or {}).get("title") or "", "표방 서비스": ai_result.get("claimed_service"),
               "페이지 목적": ai_result.get("page_purpose"), "입력 요구 정보": ai_result.get("requested_info") or ["없음"]}
    elif page and (page.get("title") or page.get("text")):
        n = config.settings()["context"]["page_text_chars"]
        out = {"제목": page.get("title") or "", "본문 일부": (page.get("text") or "")[:n],
               "입력 요구 정보": page.get("requested_info") or ["없음"]}
    if download_name:
        out["다운로드 파일"] = download_name
    return out or None


class _GeminiMatch(BaseModel):
    match: Enum("Match", {k: k for k in QUESTION["criteria"]}, type=str) = Field(description="일치 / 불일치 / 판단불가")
    reason: str = Field(description="판단 근거 한 문장")


def _gemini_match(state: dict) -> dict:
    """Jev가 실패했을 때 쓰는 대체 판정. 확률이 없어 '선택'만 돌려준다."""
    cfg = config.settings()["ai"]
    key = hashlib.sha1(json.dumps([cfg["model"], state], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cache = config.DATA_DIR / "ai_cache" / "context_gemini" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if not config.env("GEMINI_API_KEY") or not ai_judge._limiter.acquire(cfg["max_requests_per_minute"], wait=False):
        return {"status": "error", "error": "Gemini 사용 불가"}
    from google.genai import types
    system = QUESTION["instructions"] + "\nChoices:\n" + "\n".join(f"- {k}: {v}" for k, v in QUESTION["criteria"].items())
    try:
        resp = ai_judge._get_client().models.generate_content(
            model=cfg["model"], contents=json.dumps(state, ensure_ascii=False),
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               response_schema=_GeminiMatch, temperature=0,
                                               seed=cfg.get("seed", 0), candidate_count=1))
        r = _GeminiMatch.model_validate_json(resp.text).model_dump(mode="json")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Gemini 판정 실패 ({type(e).__name__})"}
    out = {"status": "ok", "choice": r["match"], "reason": r["reason"]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


STICKER_QUESTION = {
    "type": "noul",
    "instructions": "Does the QR code in `QR 안내문 사진` look like a separate sticker pasted over the original printed "
                    "notice (edges, shadow, tilt, different paper texture)?",
}


def _jev_image_fallback(out: dict, image_id: str, summary: dict, cfg: dict) -> None:
    """Gemini가 안내문을 읽지 못했을 때(한도·서버 오류): Jev에 안내문 사진을 직접 넣어 목적 일치와 스티커를 묻는다.
    Jev 이미지 입력은 공식 문서상 실험 기능이라 대체 수단으로만 쓴다. (검증: eval/jev_results.md)"""
    data = notice._shrink(image_path(image_id))
    if data is None:
        return
    ref = "notice-photo-" + hashlib.sha1(data).hexdigest()[:12]
    res = jev.ask({"QR 안내문 사진": ref, "접속한 페이지": summary},
                  {"match": QUESTION, "sticker": STICKER_QUESTION}, images={ref: data})
    if res["status"] != "ok":
        out["message"] += f", {res['error']}"
        return
    a = res["answers"]["match"]
    p = round(a.get("probabilities", {}).get("불일치", 0.0), 3)
    p_sticker = res["answers"]["sticker"]["noul"]
    out.update(status="ok", engine="Jev (사진 직접)", decision=a["choice"], p_mismatch=p,
               notice_purpose="사진에서 직접 판단")
    line = f"안내문 사진과 페이지 목적 '{out['page_purpose'] or '알 수 없음'}' 비교 (불일치 확률 {p:.2f}, 안내문 글자 읽기 실패로 사진 직접 판단)"
    if p >= cfg["mismatch_strong"]:
        out["signals"].append(make_signal("ai_context_mismatch", line, source="AI 판단"))
    elif p >= cfg["mismatch_check"]:
        out["signals"].append(make_signal("ai_context_unclear", line, source="AI 판단"))
    if p_sticker >= cfg.get("jev_sticker_threshold", 0.8):
        out["signals"].append(make_signal("qr_sticker", f"QR이 덧붙인 스티커로 보임 (확률 {p_sticker:.2f})", source="AI 판단"))
    out["message"] = ("안내문 목적과 페이지 목적이 다름" if p >= cfg["mismatch_strong"] else
                      "안내문 목적과 페이지 목적이 다를 수 있음" if p >= cfg["mismatch_check"] else
                      "안내문 목적과 페이지 목적이 일치") + " (안내문 사진을 직접 판단)"


def _empty(status: str, message: str) -> dict:
    return {"status": status, "message": message, "notice": None, "notice_status": None,
            "notice_purpose": None, "page_purpose": None, "decision": None, "p_mismatch": None,
            "engine": None, "reason": None, "signals": []}


def analyze_context(image_id: str | None, *, page: dict | None = None, ai_result: dict | None = None,
                    download_name: str | None = None, fetch_note: str | None = None) -> dict:
    """QR 안내문과 접속 페이지의 목적이 맞는지 판정한다. 실패해도 예외 없이 status 로 알린다.

    반환: status(ok/no_context/qr_only/unreadable/error), message, notice(안내문 읽기 결과), notice_purpose, page_purpose,
          decision(일치/불일치/판단불가), p_mismatch(불일치 확률), engine(Jev/Gemini), signals(점수 신호 목록)
    """
    cfg = config.settings()["context"]
    if image_path(image_id) is None:
        return _empty("no_context", "비교할 QR 안내문 사진 없음 (링크 직접 입력 또는 사진 없음)")
    if not cfg["enabled"]:
        return _empty("error", "맥락 비교 꺼짐")
    # 생략 기준 1단계: QR이 사진의 대부분을 차지하면 안내문이 없는 사진으로 보고 비교하지 않는다. (Gemini 호출 없음)
    # 2단계는 아래 안내문 읽기 결과(글자 수·readable)로 거른다.
    ratio = load_meta(image_id).get("qr_ratio")
    if ratio is not None and ratio >= cfg["skip_qr_ratio"]:
        return _empty("qr_only", f"QR만 가까이 찍힌 사진이라 안내문 비교를 생략함 (QR이 화면의 {ratio:.0%})")

    read = notice.read_notice(image_id)
    out = _empty("error", "")
    out["page_purpose"] = (ai_result or {}).get("page_purpose") or ((page or {}).get("title") or None)
    if read["status"] != "ok":
        out.update(message=read.get("error") or "안내문 읽기 실패", notice_status=read.get("error"))
        summary = page_summary(page, ai_result, download_name)
        if summary and cfg.get("jev_image_fallback", True):
            _jev_image_fallback(out, image_id, summary, cfg)
        return out
    n = read["result"]
    out["notice"] = {"text": n["full_text"], "purpose": n["purpose"], "organization": n["organization"],
                     "requests_payment": n["requests_payment"], "sticker": n["sticker_suspected"]}
    out["notice_purpose"] = n["purpose"] + (f" ({n['organization']})" if n["organization"] else "")
    if n["sticker_suspected"]:
        out["signals"].append(make_signal("qr_sticker", "안내문의 QR이 원래 인쇄된 것이 아니라 덧붙인 스티커로 보임",
                                          source="AI 판단"))
    min_chars = config.settings()["notice"]["min_chars"]
    if not n["readable"] or len("".join(n["full_text"].split())) < min_chars:
        out.update(status="unreadable", message="사진에서 안내문 글자를 읽지 못해 목적을 비교하지 않음")
        return out

    summary = page_summary(page, ai_result, download_name)
    if not summary:
        why = f" (접속 결과: {fetch_note})" if fetch_note else ""
        out.update(message=f"접속한 페이지 내용을 가져오지 못해 목적을 비교하지 못함{why}")
        return out
    state = {"QR 안내문": n["full_text"], "접속한 페이지": summary}

    res = jev.ask(state, {"match": QUESTION})
    if res["status"] == "ok":
        a = res["answers"]["match"]
        out.update(status="ok", engine="Jev", decision=a["choice"],
                   p_mismatch=round(a.get("probabilities", {}).get("불일치", 0.0), 3))
    elif cfg.get("gemini_fallback", True):
        g = _gemini_match(state)
        if g["status"] != "ok":
            out.update(message=f"{res['error']}, {g['error']}")
            return out
        out.update(status="ok", engine="Gemini", decision=g["choice"], reason=g["reason"])
    else:
        out.update(message=res["error"])
        return out

    p = out["p_mismatch"]
    purpose_line = f"안내문 목적 '{out['notice_purpose']}', 페이지 목적 '{out['page_purpose'] or '알 수 없음'}'"
    if out["engine"] == "Jev":
        if p >= cfg["mismatch_strong"]:
            out["signals"].append(make_signal("ai_context_mismatch", f"{purpose_line} (불일치 확률 {p:.2f})",
                                              source="AI 판단"))
        elif p >= cfg["mismatch_check"]:
            out["signals"].append(make_signal("ai_context_unclear", f"{purpose_line} (불일치 확률 {p:.2f})",
                                              source="AI 판단"))
    elif out["decision"] == "불일치":
        out["signals"].append(make_signal("ai_context_mismatch", f"{purpose_line} (Gemini 대체 판정)",
                                          source="AI 판단"))
    out["message"] = {"일치": "안내문 목적과 페이지 목적이 일치", "불일치": "안내문 목적과 페이지 목적이 다름",
                      "판단불가": "정보가 부족해 판단하지 못함"}.get(out["decision"], "")
    if out["signals"] and any(s["id"] == "ai_context_unclear" for s in out["signals"]):
        out["message"] = "안내문 목적과 페이지 목적이 다를 수 있음"
    return out
