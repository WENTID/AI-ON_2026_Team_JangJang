"""맥락 일치 분석(2단계) 테스트. AI(Gemini·Jev) 응답은 가짜로 바꿔 넣어 네트워크 없이 검사한다.
실행: .venv\\Scripts\\python -m pytest
"""
import pytest

from app import context, jev, notice
from app.analyzer import analyze
from app.scoring import score
from tests.test_rules import fake_snapshot

EVENT_HTML = """<html><head><title>가을맞이 경품 이벤트</title></head><body>
<h1>경품 이벤트 응모</h1><form action="/submit"><input name="name" placeholder="이름">
<input type="tel" name="phone" placeholder="휴대폰"><input name="addr" placeholder="주소"></form></body></html>"""

PARKING_NOTICE = {"full_text": "공영주차장 요금 정산 안내\n출차 전 QR코드를 스캔하여 주차요금을 결제해 주세요.",
                  "purpose": "주차요금 결제", "organization": "한빛시설관리공단", "requests_payment": True,
                  "sticker_suspected": False, "readable": True}


@pytest.fixture
def fake_ai(monkeypatch):
    """안내문 읽기·Jev 응답을 바꿔 넣는 도구. set(notice=..., p=불일치 확률, jev_error=...)"""
    state = {"notice": dict(PARKING_NOTICE), "p": 0.95, "jev_error": False, "gemini": "불일치", "jev_calls": []}
    monkeypatch.setattr(context, "image_path", lambda image_id: "x" if image_id else None)
    monkeypatch.setattr(notice, "read_notice", lambda image_id: {"status": "ok", "result": state["notice"]})

    def fake_ask(st, questions, **kw):
        state["jev_calls"].append(st)
        if state["jev_error"]:
            return {"status": "error", "error": "Jev 분석 불가 (TypeSafeRateLimitError)"}
        p = state["p"]
        choice = "불일치" if p >= 0.5 else "일치"
        return {"status": "ok", "answers": {"match": {"type": "choice", "choice": choice,
                                                      "probabilities": {"일치": 1 - p, "불일치": p, "판단불가": 0.0}}},
                "input_tokens": 600}
    monkeypatch.setattr(jev, "ask", fake_ask)
    monkeypatch.setattr(context, "_gemini_match", lambda st: {"status": "ok", "choice": state["gemini"], "reason": "테스트"})
    return state


def ids(r):
    return [s["id"] for s in r["signals"]]


def test_no_image_means_no_context():
    r = context.analyze_context(None)
    assert r["status"] == "no_context" and r["signals"] == []


def test_mismatch_probability_thresholds(fake_ai):
    page = {"title": "가을맞이 경품 이벤트", "text": "경품 이벤트 응모", "requested_info": ["이름", "휴대폰번호", "주소"]}
    fake_ai["p"] = 0.95
    assert ids(context.analyze_context("img1", page=page)) == ["ai_context_mismatch"]
    fake_ai["p"] = 0.65
    r = context.analyze_context("img1", page=page)
    assert ids(r) == ["ai_context_unclear"] and "다를 수 있음" in r["message"]
    fake_ai["p"] = 0.2
    r = context.analyze_context("img1", page=page)
    assert ids(r) == [] and r["decision"] == "일치" and r["engine"] == "Jev"


def test_jev_input_uses_ai_page_summary(fake_ai):
    ai_result = {"claimed_service": None, "page_purpose": "경품 이벤트 응모", "requested_info": ["이름", "주소"]}
    context.analyze_context("img1", page={"title": "이벤트", "text": "..."}, ai_result=ai_result)
    sent = fake_ai["jev_calls"][-1]
    assert sent["QR 안내문"].startswith("공영주차장")
    assert sent["접속한 페이지"]["페이지 목적"] == "경품 이벤트 응모"


def test_jev_failure_falls_back_to_gemini(fake_ai):
    fake_ai["jev_error"] = True
    r = context.analyze_context("img1", page={"title": "이벤트", "text": "경품 응모"})
    assert r["engine"] == "Gemini" and ids(r) == ["ai_context_mismatch"]
    fake_ai["gemini"] = "일치"
    assert ids(context.analyze_context("img1", page={"title": "주차 정산", "text": "요금 결제"})) == []


def test_unreadable_notice_skips_comparison_but_keeps_sticker(fake_ai):
    fake_ai["notice"] = {**PARKING_NOTICE, "full_text": "QR", "readable": False, "sticker_suspected": True}
    r = context.analyze_context("img1", page={"title": "이벤트", "text": "경품"})
    assert r["status"] == "unreadable" and ids(r) == ["qr_sticker"] and fake_ai["jev_calls"] == []


def test_gemini_read_failure_falls_back_to_jev_image(fake_ai, monkeypatch):
    """Gemini가 안내문을 못 읽으면(한도·서버 오류) Jev에 사진을 직접 넣어 판정한다."""
    monkeypatch.setattr(notice, "read_notice", lambda image_id: {"status": "error", "error": "안내문 읽기 실패 (TimeoutError)"})
    monkeypatch.setattr(notice, "_shrink", lambda path: b"jpeg-bytes")
    seen = {}

    def fake_ask(st, questions, images=None, **kw):
        seen.update(state=st, questions=questions, images=images)
        return {"status": "ok", "answers": {"match": {"type": "choice", "choice": "불일치",
                                                      "probabilities": {"일치": 0.02, "불일치": 0.97}},
                                            "sticker": {"type": "noul", "noul": 0.9}}}
    monkeypatch.setattr(jev, "ask", fake_ask)
    r = context.analyze_context("img1", page={"title": "이벤트", "text": "경품 응모"})
    assert r["status"] == "ok" and r["engine"] == "Jev (사진 직접)"
    assert ids(r) == ["ai_context_mismatch", "qr_sticker"]
    ref = seen["state"]["QR 안내문 사진"]
    assert seen["images"] == {ref: b"jpeg-bytes"} and set(seen["questions"]) == {"match", "sticker"}


def test_error_page_is_not_compared(fake_ai):
    """404 오류 화면을 페이지 목적으로 오해해 '불일치'를 내지 않는다. (스티커 신호는 유지)"""
    snap = fake_snapshot("https://qr-demo.onrender.com/demo-site/none", "<html><title>404 Not Found</title><body>Not Found</body></html>")
    snap["trace"]["status"] = 404
    fake_ai["notice"] = {**PARKING_NOTICE, "sticker_suspected": True}
    r = analyze(snap, use_ai=False, context_image_id="img1")
    c = r["context"]
    assert c["status"] == "error" and "HTTP 404" in c["message"] and fake_ai["jev_calls"] == []
    assert [s["id"] for s in c["signals"]] == ["qr_sticker"]


def test_sticker_and_mismatch_combo_scores_danger():
    from app.scoring import make_signal
    r = score([make_signal("hosting_platform", ""), make_signal("valid_cert", ""),
               make_signal("ai_context_mismatch", ""), make_signal("qr_sticker", "")])
    assert "sticker_context_mismatch" in [x["id"] for x in r["risk_reasons"]]
    assert r["verdict"] == "danger"


def test_demo_case_url_safe_but_context_catches(fake_ai):
    """시연 사례: 무료 호스팅의 평범한 이벤트 페이지. URL·페이지 검사만으로는 '안전(정황 미약)',
    주차요금 안내문 사진과 함께 보내면 맥락 불일치로 '경고'."""
    snap = fake_snapshot("https://qr-demo.onrender.com/demo-site/event", EVENT_HTML, created="2024-01-01T00:00:00Z")
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    plain = analyze(snap, use_ai=False)
    assert plain["verdict"] == "safe" and plain["safe_level"] == "minor"
    with_ctx = analyze(snap, use_ai=False, context_image_id="img1")
    assert with_ctx["verdict"] == "caution" and with_ctx["top_rule"]["id"] == "ai_analysis"
    assert "목적이 다릅니다" in with_ctx["one_liner"]
    checks = {k["id"]: k for c in with_ctx["report"] for s in c["sections"] for k in s["checks"]}
    assert checks["context"]["status"] == "danger" and checks["qr_sticker"]["status"] == "safe"


def test_sticker_only_one_liner_is_not_purpose_mismatch(fake_ai):
    """스티커만 걸리고 목적은 일치할 때 '목적이 다르다'는 대표 문구가 나오면 안 된다. (11/23 버그)"""
    fake_ai["notice"] = {**PARKING_NOTICE, "sticker_suspected": True}
    fake_ai["p"] = 0.05
    snap = fake_snapshot("https://qr-demo.onrender.com/demo-site/event", EVENT_HTML, created="2024-01-01T00:00:00Z")
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False, context_image_id="img1")
    assert "목적이 다릅니다" not in r["one_liner"] and "스티커" in r["one_liner"]


def test_failed_context_shows_unknown_not_na(fake_ai):
    """목적 비교를 하려다 실패하면(Jev·Gemini 모두 실패) '해당 없음'이 아니라 '확인 불가'."""
    fake_ai["jev_error"] = True
    import app.context as ctx_mod
    ctx_mod._gemini_match = lambda st: {"status": "error", "error": "Gemini 사용 불가"}
    snap = fake_snapshot("https://qr-demo.onrender.com/demo-site/event", EVENT_HTML, created="2024-01-01T00:00:00Z")
    r = analyze(snap, use_ai=False, context_image_id="img1")
    checks = {k["id"]: k for c in r["report"] for s in c["sections"] for k in s["checks"]}
    cats = {c["id"]: c for c in r["report"]}
    assert checks["context"]["status"] == "unknown"
    assert cats["ai_analysis"]["status"] == "unknown"  # 대분류에서도 실패가 '안전'에 가려지지 않음
    # 결과 카드에 실패 사유 문장
    assert any("목적을 비교하지 못했어요" in f for f in r["failures"])


def test_fetch_failure_message_groups_ai_checks():
    """페이지 접속 실패면 AI 검사들을 따로 말하지 않고 '접속하지 못해…' 한 문장으로 묶는다."""
    from app.analyzer import failure_messages
    trace = {"error": "도메인을 찾을 수 없음 (x.example)", "status": None}
    msgs = failure_messages({"ai", "context", "cross_check", "domain_age"},
                            {"ai": "분석할 페이지 내용이 없음", "context": "페이지 없음", "cross_check": "x",
                             "domain_age": "도메인 등록일 확인 불가"}, trace, fetched=False)
    assert msgs[0] == "페이지에 접속하지 못해 페이지 내용·AI 검사를 하지 못했어요 (도메인을 찾을 수 없음 (x.example))"
    assert msgs[1:] == ["도메인 등록일을 확인하지 못했어요"]
    assert failure_messages(set(), {}, {"error": None, "status": 200}, fetched=True) == []


# ---------- 맥락 분석 생략 기준 (QR 면적 비율) ----------
def test_qr_only_photo_skips_context(fake_ai, monkeypatch):
    calls = []
    monkeypatch.setattr(notice, "read_notice", lambda image_id: calls.append(image_id) or {"status": "ok", "result": PARKING_NOTICE})
    monkeypatch.setattr(context, "load_meta", lambda image_id: {"qr_ratio": 0.6})
    r = context.analyze_context("img1", page={"title": "t", "text": "x"})
    assert r["status"] == "qr_only" and r["signals"] == [] and calls == []  # Gemini 안내문 읽기도 안 함
    monkeypatch.setattr(context, "load_meta", lambda image_id: {"qr_ratio": 0.09})
    assert context.analyze_context("img1", page={"title": "t", "text": "x"})["status"] == "ok"


def test_qr_area_ratio():
    import cv2
    from app.qr_decode import qr_area_ratio
    qr = cv2.resize(cv2.QRCodeEncoder.create().encode("https://example.com/pay"), None, fx=10, fy=10,
                    interpolation=cv2.INTER_NEAREST)
    close = cv2.copyMakeBorder(qr, 20, 20, 60, 60, cv2.BORDER_CONSTANT, value=255)   # QR만 가까이
    poster = cv2.copyMakeBorder(qr, 400, 400, 300, 300, cv2.BORDER_CONSTANT, value=255)  # 큰 안내문 속 QR
    assert qr_area_ratio(close) > 0.35 > qr_area_ratio(poster) > 0
    assert qr_area_ratio(cv2.copyMakeBorder(qr[:5, :5], 0, 0, 0, 0, cv2.BORDER_CONSTANT)) is None
