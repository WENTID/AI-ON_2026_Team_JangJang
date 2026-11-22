"""2차 교차 분석 테스트. Gemini 응답은 가짜로 바꿔 넣어 네트워크 없이 검사한다.
실행: .venv\\Scripts\\python -m pytest
"""
import pytest

from app import config, cross_check
from app.scoring import make_signal

NOTICE_TEXT = "공영주차장 요금 정산 안내\n주차요금 2,000원\n문의 02-123-4567"
PAGE = {"title": "주차요금 결제", "text": "결제 금액 15,000원을 입력하세요. 고객센터 010-9999-0000", "requested_info": ["카드번호"]}
CONTEXT = {"status": "ok", "notice": {"text": NOTICE_TEXT}}


def finding(sev="높음", ne="주차요금 2,000원", pe="결제 금액 15,000원", persp="금액·요금"):
    return {"perspective": persp, "description": "안내문 금액과 페이지 금액이 다름",
            "notice_evidence": ne, "page_evidence": pe, "severity": sev}


@pytest.fixture
def mode(monkeypatch):
    """점수 반영 방식을 바꿔 가며 시험한다."""
    base = config.settings()

    def set_mode(m):
        s = {**base, "cross_check": {**base["cross_check"], "scoring": m, "enabled": True},
             "ai": {**base["ai"], "enabled": True}}
        monkeypatch.setattr(config, "settings", lambda: s)
    return set_mode


@pytest.fixture
def fake_gemini(monkeypatch):
    state = {"findings": [finding()], "calls": []}

    def fake_ask(image_id, notice_text, page_info, already):
        state["calls"].append(already)
        return {"status": "ok", "findings": state["findings"]}
    monkeypatch.setattr(cross_check, "_ask", fake_ask)
    return state


def test_quote_must_exist_in_source():
    assert cross_check.quote_found("주차요금 2,000원", NOTICE_TEXT)
    assert cross_check.quote_found("주차 요금 2000원", NOTICE_TEXT)         # 공백·쉼표 차이는 허용
    assert not cross_check.quote_found("주차요금 5,000원", NOTICE_TEXT)     # 숫자가 다르면 불통과
    kept, dropped = cross_check.verify(
        [finding(), finding(pe="계좌번호 123-45"), finding(ne="", pe="")], NOTICE_TEXT, cross_check.page_text_of(PAGE))
    assert len(kept) == 1 and len(dropped) == 2  # 지어낸 인용·근거 없는 발견은 제외


def test_display_only_shows_but_no_score(mode, fake_gemini):
    mode("display_only")
    r = cross_check.analyze_cross("img1", CONTEXT, page=PAGE, ai_result=None, final_domain="pay.example", signals=[])
    assert r["status"] == "ok" and len(r["findings"]) == 1 and r["signal"] is None


def test_weak_capped(mode, fake_gemini):
    mode("weak_capped")
    fake_gemini["findings"] = [finding(), finding(sev="중간", ne="문의 02-123-4567", pe="고객센터 010-9999-0000",
                                                  persp="연락처·계좌")]
    r = cross_check.analyze_cross("img1", CONTEXT, page=PAGE, ai_result=None, final_domain="pay.example", signals=[])
    s = r["signal"]
    assert s["id"] == "ai_cross_finding" and s["tier"] == "weak"
    assert s["points"] == config.settings()["cross_check"]["max_points"]  # 15 + 8 → 상한 15


def test_with_other_signals_needs_other_risk(mode, fake_gemini):
    mode("with_other_signals")
    args = dict(page=PAGE, ai_result=None, final_domain="pay.example")
    assert cross_check.analyze_cross("img1", CONTEXT, signals=[], **args)["signal"] is None
    other = [make_signal("new_domain", "생성 3일")]
    assert cross_check.analyze_cross("img1", CONTEXT, signals=other, **args)["signal"]["points"] > 0


def test_already_checked_items_are_given_to_ai(mode, fake_gemini):
    mode("display_only")
    other = [make_signal("ai_context_mismatch", "안내문 주차, 페이지 이벤트")]
    cross_check.analyze_cross("img1", CONTEXT, page=PAGE, ai_result=None, final_domain="pay.example", signals=other)
    already = fake_gemini["calls"][0]
    assert "신규 등록 도메인" in already                       # 정책 검사 항목
    assert "AI 판단: 안내문과 페이지 목적 불일치" in already    # 1차 맥락 검사 항목
    assert any("이번 검사에서 발견됨" in x for x in already)     # 실제로 걸린 신호
    assert not any("교차 분석" in x for x in already)           # 자기 자신은 제외


def test_skipped_without_notice(mode, fake_gemini):
    mode("weak_capped")
    for ctx in ({"status": "no_context", "notice": None}, {"status": "qr_only", "notice": None}):
        r = cross_check.analyze_cross("img1", ctx, page=PAGE, ai_result=None, final_domain="x", signals=[])
        assert r["status"] == "skipped" and fake_gemini["calls"] == []
