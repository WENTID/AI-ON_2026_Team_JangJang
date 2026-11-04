"""분석 파이프라인: 스냅샷 → 신호 수집 → (필요하면) AI 사칭 판별 → 점수·판정 → 결과.

실시간 분석과 검증 스크립트가 같은 함수를 쓴다.
검증할 때는 use_db / use_whitelist / use_ai 로 DB·신뢰 목록·AI를 켜고 끄며 효과를 비교한다.
"""
from urllib.parse import parse_qsl, urlsplit

from . import ai_judge, config, hierarchy
from .brand import brands_in_text, find_brand, find_lookalike, official_brand
from .context import analyze_context
from .lookups import age_days
from .page import SENSITIVE_KINDS, extract
from .scoring import VERDICT_LABEL, make_signal, score
from .urls import domain_label, host_matches, hostname, is_ip, registrable, subdomain, suffix

CERT_PROBLEM = {
    "expired": "인증서가 만료됨",
    "hostname_mismatch": "인증서의 도메인이 접속 주소와 다름",
    "self_signed": "공인되지 않은(자체 서명) 인증서",
    "untrusted": "신뢰할 수 없는 기관이 발급한 인증서",
    "other": "인증서 오류",
}


def url_structure_signals(url: str) -> list[dict]:
    """URL 모양만 보고 판단하는 약한 신호들."""
    spec = config.scoring()["signals"]
    parts = urlsplit(url)
    host = hostname(url)
    found = []
    if len(url) >= spec["long_url"]["min_length"]:
        found.append(make_signal("long_url", f"URL 길이가 {len(url)}자로 매우 김"))
    depth = len([p for p in subdomain(host).split(".") if p]) if not is_ip(host) else 0
    if depth >= spec["many_subdomains"]["min_count"]:
        found.append(make_signal("many_subdomains", f"서브도메인이 {depth}단계로 많음 ({host})"))
    encoded = url.count("%") + sum(url.count(c) for c in "~!$*'|^`")
    if encoded >= spec["encoded_chars"]["min_count"]:
        found.append(make_signal("encoded_chars", f"인코딩·특수문자가 {encoded}개 들어 있음"))
    if "@" in parts.netloc:
        found.append(make_signal("at_symbol", "주소에 @가 있어 실제 접속 주소를 헷갈리게 함"))
    n_params = len(parse_qsl(parts.query, keep_blank_values=True))
    if n_params >= spec["many_params"]["min_count"]:
        found.append(make_signal("many_params", f"파라미터가 {n_params}개로 많음"))
    if "xn--" in host:
        found.append(make_signal("punycode", f"특수 문자로 만든 국제화 도메인 ({host})"))
    tld = suffix(host).rsplit(".", 1)[-1] if not is_ip(host) else ""
    if tld in spec["suspicious_tld"]["tlds"]:
        found.append(make_signal("suspicious_tld", f"악용이 잦은 최상위 도메인(.{tld})"))
    return found


def ai_signal(result: dict, final_host: str) -> tuple[dict | None, str]:
    """AI 판단 결과와 실제 도메인을 비교해 신호를 만든다. (신호, 화면 표시용 설명)"""
    cfg = config.settings()["ai"]
    claimed = result.get("claimed_service")
    final_reg = registrable(final_host)
    if not claimed or result.get("confidence", 0) < cfg["min_confidence"]:
        return None, "특정 서비스를 표방하지 않는 페이지로 판단"
    brand = find_brand(claimed)
    guess = (result.get("official_domain_guess") or "").lower().strip()
    if brand:
        # 브랜드 목록에 있는 서비스(은행·택배 등)는 공식 도메인 목록으로만 엄격하게 비교한다. (AI가 속는 것 방지)
        officials = brand["official_domains"]
        genuine = host_matches(final_host, officials)
    elif guess:
        # 목록에 없는 서비스는 회사가 여러 도메인을 쓰는 경우가 많아 AI가 추정한 대표 도메인 하나로는 부족하다.
        # 도메인 이름이 같거나, AI가 실제 도메인을 그 회사 소유(테스트·데모·자회사 도메인 포함)로 판단하면 공식으로 본다.
        officials = [registrable(guess.split("/")[0])]
        genuine = (host_matches(final_host, officials) or domain_label(officials[0]) == domain_label(final_reg)
                   or result.get("domain_matches_service") is True)
    else:
        return None, f"'{claimed}'를 표방하지만 공식 도메인을 알 수 없어 비교하지 못함"
    if genuine:
        return None, f"'{claimed}' 운영 주체의 도메인으로 판단"
    sensitive = [i for i in result.get("requested_info", []) if i in SENSITIVE_KINDS]
    # 목록에 없는 서비스는 AI가 '운영 주체 도메인이 아님'이라고 분명히 답했을 때만 강한 신호로 반영한다.
    sure_mismatch = brand is not None or result.get("domain_matches_service") is False
    if sensitive and sure_mismatch:
        return make_signal("ai_impersonation",
                           f"'{claimed}'를 표방하며 {', '.join(sensitive)}를 요구하지만, 실제 도메인({final_reg})은 "
                           f"공식 도메인({officials[0]})이 아님", source="AI 판단"), "사칭 의심"
    return make_signal("ai_brand_claim",
                       f"'{claimed}'를 표방하지만 실제 도메인({final_reg})은 공식 도메인({officials[0]})이 아님",
                       source="AI 판단"), "공식 도메인 아님"


def headline(phishing_verdict: str, malware: bool, notice_count: int) -> dict:
    """결과 화면 맨 위 문구. 피싱 판정과 기타 위협(악성코드 등)을 섞지 않는다."""
    if malware and phishing_verdict == "danger":
        return {"kind": "피싱 + 악성코드 위험",
                "message": "피싱 사이트일 가능성이 높고, 악성·원치 않는 소프트웨어 배포 사이트로도 등록되어 있습니다. 접속하지 마세요."}
    if malware:
        return {"kind": "악성코드 위험",
                "message": "피싱 신호는 뚜렷하지 않지만, 악성·원치 않는 소프트웨어를 퍼뜨리는 사이트로 등록되어 있습니다. "
                           "접속하거나 파일을 받지 마세요."}
    if phishing_verdict == "danger":
        return {"kind": "피싱 위험",
                "message": "피싱 사이트일 가능성이 높습니다. 아이디·비밀번호·금융정보를 입력하지 마세요."}
    if phishing_verdict == "caution":
        return {"kind": "피싱 주의",
                "message": "피싱으로 단정할 수는 없지만 의심 신호가 있습니다. 개인정보를 입력하기 전에 주소를 다시 확인하세요."}
    message = "피싱 의심 신호가 발견되지 않았습니다."
    if notice_count:
        message += f" 다만 피싱과 별개로 확인이 필요한 점이 {notice_count}건 있습니다."
    return {"kind": "피싱 안전", "message": message}


def analyze(snap: dict, *, use_ai: bool = True, use_db: bool = True, use_whitelist: bool = True,
            ai_wait: bool = False, context_image_id: str | None = None) -> dict:
    settings = config.settings()
    spec = config.scoring()["signals"]
    trace = snap["trace"]
    input_url, final_url = snap["url"], trace["final_url"]
    in_host, fin_host = hostname(input_url), hostname(final_url)
    in_reg, fin_reg = registrable(in_host), registrable(fin_host)
    page = extract(snap["html"], final_url) if snap.get("html") else None
    signals: list[dict] = []
    notices: list[dict] = []

    # 검사 순서는 판정 위계(config/rules_hierarchy.json)의 예방 수칙 순서를 따른다.
    # 단, AI 사칭 판별은 규칙 점수로 호출 여부를 정하므로 규칙 검사가 모두 끝난 뒤에 한다.

    # ======== 수칙 1. 접속 주소가 공식 주소인지 확인 ========
    # ---- 피싱 DB ----
    sb = snap["safe_browsing"]
    if use_db:
        sources = []
        if any(m["threat_type"] == "SOCIAL_ENGINEERING" for m in sb["matches"]):
            sources.append("Google Safe Browsing")
        if snap["phish_feed"]["matches"]:
            sources.append("OpenPhish")
        if sources:
            signals.append(make_signal("phishing_db", f"{', '.join(sources)} 피싱 목록에 등록된 주소", source="DB"))

    # ---- URL 구조 ----
    seen = set()
    for url in dict.fromkeys([input_url, final_url]):
        for sig in url_structure_signals(url):
            if sig["id"] not in seen:
                seen.add(sig["id"])
                signals.append(sig)
    if is_ip(in_host) or is_ip(fin_host):
        signals.append(make_signal("ip_host", f"도메인 이름 대신 IP 주소({fin_host if is_ip(fin_host) else in_host})로 접속"))
    if host_matches(in_host, settings["shorteners"]) and fin_reg != in_reg:
        same_brand = official_brand(in_host) and official_brand(in_host) is official_brand(fin_host)
        if not same_brand and not trace["error"]:
            signals.append(make_signal("shortener_cross_domain", f"단축 URL({in_host})이 다른 도메인({fin_reg})으로 연결됨"))
    hosted = host_matches(fin_host, settings["hosting_platforms"])
    if hosted:
        signals.append(make_signal("hosting_platform", f"누구나 페이지를 만들 수 있는 무료 호스팅 주소({fin_host})"))

    # ---- 신뢰 사이트 ----
    trusted = use_whitelist and not hosted and not is_ip(fin_host) and host_matches(fin_host, config.whitelist())
    if trusted:
        signals.append(make_signal("trusted_site", f"신뢰 사이트 목록에 있는 도메인({fin_reg})"))

    # ---- 도메인 나이 ----
    domain = snap["domain"]
    age = age_days(domain.get("created"), snap["collected_at"])
    if age is not None and age < spec["new_domain"]["max_age_days"]:
        signals.append(make_signal("new_domain", f"도메인이 {max(age, 0)}일 전에 생성됨 ({fin_reg})"))
    elif age is not None and age >= spec["old_domain"]["min_age_days"] and not hosted:
        signals.append(make_signal("old_domain", f"도메인이 {age // 365}년 전에 생성됨"))

    # ---- 브랜드 유사 도메인 (신뢰 사이트는 제외) ----
    if not trusted:
        for host in dict.fromkeys([fin_host, in_host]):
            hit = find_lookalike(host)
            if hit:
                signals.append(make_signal("brand_lookalike", hit["how"]))
                break

    # ======== 수칙 2. 개인정보·금융정보 입력 요구 주의 ========
    # ---- 연결 보안 (단독으로 판정을 바꾸지 않음) ----
    if urlsplit(final_url).scheme == "http":
        signals.append(make_signal("http", "암호화되지 않은 연결(http)"))
        notices.append({"id": "http", "detail": "암호화되지 않은 연결(http)이라 입력한 정보가 노출될 수 있음"})
    else:
        cert = snap["cert"]
        if cert.get("valid"):
            signals.append(make_signal("valid_cert", "유효한 HTTPS 인증서"))
        elif cert.get("checked"):
            notices.append({"id": "cert_error", "detail": CERT_PROBLEM.get(cert.get("kind"), "인증서 오류")})

    # ---- 페이지 내용 (입력 폼) ----
    if page and page["sensitive_info"]:
        kinds = ", ".join(page["sensitive_info"])
        signals.append(make_signal("sensitive_form", f"{kinds} 입력란이 있음"))
        if not trusted:
            claimed = [b for b in brands_in_text(page["title"]) if not host_matches(fin_host, b["official_domains"])]
            if claimed:
                b = claimed[0]
                signals.append(make_signal("brand_form_mismatch",
                                           f"'{b['name']}'를 내세운 페이지에서 {kinds}를 입력받지만 "
                                           f"공식 도메인({b['official_domains'][0]})이 아님"))
    if page and page["external_form_domains"] and not trusted:
        signals.append(make_signal("form_posts_elsewhere",
                                   f"입력한 정보가 다른 도메인({', '.join(page['external_form_domains'][:2])})으로 전송됨"))

    # ======== 수칙 3. 앱 설치·파일 다운로드 유도 주의 (점수와 별개인 주의사항) ========
    if trace["is_download"]:
        path = urlsplit(final_url).path
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        name = "앱 설치 파일(.apk)" if ext in ("apk", "xapk") else (f"실행 파일(.{ext})" if ext else "파일")
        notices.append({"id": "download", "detail": f"{name} 다운로드로 연결됨"})
    if use_db and any(m["threat_type"] != "SOCIAL_ENGINEERING" for m in sb["matches"]):
        notices.append({"id": "malware_db", "escalate": True,
                        "detail": "악성·원치 않는 소프트웨어 배포 사이트로 등록됨 (Google Safe Browsing)"})

    # ======== 수칙 1 (계속). AI 사칭 판별 (규칙만으로 애매할 때만) ========
    pre = score(signals)
    ai = {"status": "skipped", "message": "", "result": None, "model": None}
    if not use_ai or not settings["ai"]["enabled"]:
        ai["message"] = "AI 분석 꺼짐"
    elif any(s["tier"] == "confirmed" for s in signals):
        ai["message"] = "피싱 DB 등재로 AI 분석 생략"
    elif trusted:
        ai["message"] = "신뢰 사이트라 AI 분석 생략"
    elif not page or not (page["title"] or page["text"]):
        ai["message"] = "분석할 페이지 내용이 없음"
    elif pre["score"] >= settings["ai"]["skip_if_rule_score_at_least"]:
        ai["message"] = "규칙 점수만으로 위험이 확실해 AI 분석 생략"
    else:
        res = ai_judge.judge_page(page, fin_host, wait=ai_wait)
        ai.update(status=res["status"], model=res["model"])
        if res["status"] == "ok":
            ai["result"] = res["result"]
            sig, ai["message"] = ai_signal(res["result"], fin_host)
            if sig:
                signals.append(sig)
        else:
            ai["message"] = res["error"]

    # ======== 수칙 4. 출처가 불분명한 QR 주의 — QR 안내문 맥락 (2단계) ========
    context = analyze_context(context_image_id, ai["result"]["page_purpose"] if ai["result"] else None)
    if context["signal"]:
        signals.append(context["signal"])

    # ---- 최종 판정 ----
    # 피싱 판정과 기타 위협은 따로 계산하고 따로 표시한다.
    # 악성코드 DB 등재만 종합 판정을 '위험'으로 올리되, 화면 문구는 '피싱'이 아니라 '악성코드'로 안내한다.
    final = score(signals)
    phishing_verdict = final["verdict"]
    malware = any(n.get("escalate") for n in notices)
    verdict = "danger" if malware else phishing_verdict
    risk_reasons = [r for r in final["risk_reasons"] if r["tier"] != "fact"]
    rules = hierarchy.build(verdict=verdict, phishing_verdict=phishing_verdict, risk_reasons=risk_reasons,
                            safe_reasons=final["safe_reasons"], notices=notices)

    return {
        "input_url": input_url,
        "final_url": final_url,
        "redirects": trace["chain"],
        "verdict": verdict,
        "verdict_label": VERDICT_LABEL[verdict],
        "phishing_verdict": phishing_verdict,
        "phishing_verdict_label": VERDICT_LABEL[phishing_verdict],
        "headline": headline(phishing_verdict, malware, len(notices)),
        "score": final["score"],
        "breakdown": final["breakdown"],
        # 판정 위계: 범죄유형, 대표 예방 수칙 한 줄, 행동 안내, 수칙별로 묶은 근거
        "crime_type": rules["crime_type"],
        "top_rule": rules["top_rule"],
        "one_liner": rules["one_liner"],
        "action": rules["action"],
        "groups": rules["groups"],
        "risk_reasons": risk_reasons,
        "safe_reasons": final["safe_reasons"],
        "notices": notices,
        "ai": ai,
        "context": context,
        "page": {"title": page["title"], "requested_info": page["requested_info"]} if page else None,
        "checks": {
            "fetch": trace["error"] or f"HTTP {trace['status']}",
            "domain_created": domain.get("created") if domain.get("checked") else f"확인 불가 ({domain.get('error')})",
            "domain_age_days": age,
            "cert": "해당 없음(http)" if urlsplit(final_url).scheme == "http" else (
                "정상" if snap["cert"].get("valid") else (snap["cert"].get("error") or "확인 불가")),
            "safe_browsing": ("등재: " + ", ".join(m["threat_type"] for m in sb["matches"]) if sb["matches"]
                              else "미등재") if sb["checked"] else f"확인 불가 ({sb['error']})",
            "phish_feed": ("등재" if snap["phish_feed"]["matches"] else "미등재") if snap["phish_feed"]["checked"]
            else f"확인 불가 ({snap['phish_feed']['error']})",
        },
        "screenshot": None,
    }
