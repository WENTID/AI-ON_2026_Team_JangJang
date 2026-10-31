"""서버나 API 키 없이 돌아가는 단위 테스트."""

import io

import qrcode

from core.analyzer import parse_response
from core.baseline import judge_url_only
from core.page_extract import classify_input, extract_page
from core.qr_decode import decode_qr
from core.url_features import extract_features, levenshtein, load_brands
from core.url_trace import find_client_redirect


def _qr_png(text: str) -> bytes:
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return buf.getvalue()


# ── qr_decode ──
def test_decode_qr_url():
    r = decode_qr(_qr_png("https://saesol-parking.com/pay"))
    assert r["url"] == "https://saesol-parking.com/pay"
    assert r["error"] is None


def test_decode_qr_not_url():
    r = decode_qr(_qr_png("그냥 글자"))
    assert r["url"] is None and r["raw"] == "그냥 글자"


def test_decode_qr_bad_image():
    assert decode_qr(b"not an image")["error"]


# ── url_features ──
def test_levenshtein():
    assert levenshtein("kakaopay", "kakaopay") == 0
    assert levenshtein("kakaopay", "kakaopey") == 1


def test_features_basic():
    f = extract_features("https://order.minttable.xyz/t/7", load_brands())
    assert f["registered_domain"] == "minttable.xyz"
    assert f["is_cheap_tld"] and f["is_https"] and f["subdomain_depth"] == 1
    assert f["lookalike_of"] is None


def test_features_lookalike_keyword():
    f = extract_features("https://kakaopay-secure.net/login", load_brands())
    assert f["lookalike_of"] == "카카오페이"


def test_features_official_not_lookalike():
    f = extract_features("https://www.kakaopay.com/", load_brands())
    assert f["lookalike_of"] is None


def test_features_ip_and_at():
    f = extract_features("http://user@192.168.0.1/login", [])
    assert f["is_ip"] and f["has_at"] and not f["is_https"]


# ── baseline ──
def test_baseline_safe_and_danger():
    brands = load_brands()
    assert judge_url_only(extract_features("https://parking.saesol.go.kr/pay", brands))["verdict"] == "SAFE"
    assert judge_url_only(extract_features("https://kakaopay-secure.net/login", brands))["verdict"] == "DANGER"


def test_baseline_uses_max_of_two():
    brands = load_brands()
    r = judge_url_only([extract_features("https://han.gl/ab12", brands),
                        extract_features("https://order.minttable.xyz/t/7", brands)])
    assert r["verdict"] == "CAUTION"


# ── url_trace (HTML 리다이렉트 감지) ──
def test_client_redirect_detection():
    assert find_client_redirect('<meta http-equiv="refresh" content="3; url=https://a.com/x">') == "https://a.com/x"
    assert find_client_redirect('<script>location.href = "/next";</script>') == "/next"
    assert find_client_redirect("<p>no redirect</p>") is None


# ── page_extract ──
def test_classify_input():
    assert classify_input("카드 비밀번호 앞 2자리") == "card_password"
    assert classify_input("cvc") == "card_cvc"
    assert classify_input("계좌 비밀번호") == "bank_account"
    assert classify_input("아이디") == "login"
    assert classify_input("휴대폰으로 받은 인증번호") == "otp_cert"
    assert classify_input("차량번호") == "vehicle"
    assert classify_input("요청사항") == "other"


def test_extract_page_form_other_domain():
    html = """<html><head><title>t</title></head><body>
      <p>과태료 미납 10분 이내 납부</p>
      <form action="https://evil.example/collect"><input name="card_no" placeholder="카드번호"></form>
      <a href="/files/app.apk">앱 설치</a></body></html>"""
    p = extract_page(html, "https://shop.example.com/pay")
    assert p["forms"][0]["same_domain"] is False
    assert p["requested_info"] == ["card_number"]
    assert any("APK" in x for x in p["app_install_links"])
    assert "과태료" in p["urgency_phrases"] and "10분 이내" in p["urgency_phrases"]


# ── analyzer (응답 파싱) ──
def test_parse_response_with_codeblock():
    text = '```json\n{"verdict": "danger", "evidence": ["a"], "advice": "b"}\n```'
    d = parse_response(text)
    assert d["verdict"] == "DANGER" and d["mismatches"] == []


def test_parse_response_invalid():
    assert parse_response("판정할 수 없습니다") is None
    assert parse_response('{"verdict": "MAYBE", "evidence": [], "advice": ""}') is None
