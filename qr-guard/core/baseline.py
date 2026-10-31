"""URL만 검사하는 비교 기준 (baseline).

"기존 URL 검사기가 하는 수준"을 흉내 낸 점수 규칙이다.
페이지 내용과 안내 문구는 보지 않는다.

※ 일부러 약하게 만들지 않는다. "URL 검사도 이 정도는 잡는다, 그런데 이건
  못 잡는다"를 공정하게 보여줘야 평가 결과에 설득력이 있다.
"""

# ── 점수와 기준값 (여기만 고치면 된다) ─────────────────────────────
SCORE_LOOKALIKE = 3        # 유사 도메인
SCORE_IP = 3               # IP 주소로 접속
SCORE_PUNYCODE = 2         # punycode (xn--) 포함
SCORE_AT = 2               # URL 에 @ 포함
SCORE_CHEAP_TLD = 1        # 저가 TLD
SCORE_NO_HTTPS = 1         # https 아님
SCORE_SHORTENER = 1        # 단축 URL
SCORE_DEEP_SUBDOMAIN = 1   # 서브도메인 깊이 기준 이상
SCORE_LONG_URL = 1         # URL 길이 기준 이상
SCORE_APK = 3              # .apk 다운로드

SUBDOMAIN_DEPTH_LIMIT = 3  # 이 깊이 이상이면 가점
URL_LENGTH_LIMIT = 100     # 이 길이 이상이면 가점

DANGER_THRESHOLD = 3       # 합계 3점 이상 → DANGER
CAUTION_THRESHOLD = 1      # 1~2점 → CAUTION, 0점 → SAFE
# ────────────────────────────────────────────────────────────────


def score_url(f: dict) -> tuple[int, list[str]]:
    """URL 특징 하나에 대한 (점수, 이유 목록)."""
    score, reasons = 0, []

    def add(points: int, reason: str):
        nonlocal score
        score += points
        reasons.append(f"{reason} (+{points})")

    if f.get("lookalike_of"):
        add(SCORE_LOOKALIKE, f"'{f['lookalike_of']}' 유사 도메인 의심: {f.get('host')}")
    if f.get("is_ip"):
        add(SCORE_IP, "도메인 대신 IP 주소로 접속")
    if f.get("has_punycode"):
        add(SCORE_PUNYCODE, "punycode(xn--) 도메인")
    if f.get("has_at"):
        add(SCORE_AT, "URL 에 '@' 포함")
    if f.get("is_cheap_tld"):
        add(SCORE_CHEAP_TLD, f"저가 TLD(.{f.get('tld')}) 사용")
    if f.get("url") and not f.get("is_https"):
        add(SCORE_NO_HTTPS, "https 가 아님")
    if f.get("is_shortener"):
        add(SCORE_SHORTENER, f"단축 URL 서비스({f.get('registered_domain')}) 사용")
    if f.get("subdomain_depth", 0) >= SUBDOMAIN_DEPTH_LIMIT:
        add(SCORE_DEEP_SUBDOMAIN, f"서브도메인 깊이 {f['subdomain_depth']}")
    if f.get("url_length", 0) >= URL_LENGTH_LIMIT:
        add(SCORE_LONG_URL, f"URL 길이 {f['url_length']}자")
    if f.get("is_apk") or f.get("is_download"):
        add(SCORE_APK, "앱 설치 파일(.apk) 다운로드")
    return score, reasons


def judge_url_only(features) -> dict:
    """URL 특징만으로 판정한다. baseline 은 UNKNOWN 을 내지 않는다.

    features: 특징 dict 하나, 또는 [최초 URL 특징, 최종 URL 특징] 리스트.
              리스트면 점수가 높은 쪽을 쓴다.
    반환: {"verdict": str, "reasons": [str], "score": int}
    """
    try:
        items = features if isinstance(features, list) else [features]
        items = [f for f in items if f]
        best_score, best_reasons = 0, []
        for f in items:
            s, r = score_url(f)
            if s > best_score:
                best_score, best_reasons = s, r

        if best_score >= DANGER_THRESHOLD:
            verdict = "DANGER"
        elif best_score >= CAUTION_THRESHOLD:
            verdict = "CAUTION"
        else:
            verdict = "SAFE"
        if not best_reasons:
            best_reasons = ["URL 에서 의심 신호를 찾지 못함"]
        return {"verdict": verdict, "reasons": best_reasons, "score": best_score}
    except Exception as e:
        return {"verdict": "SAFE", "reasons": [f"baseline 오류: {e}"], "score": 0}
