"""URL 특징 추출.

baseline(URL만 보는 규칙 판정)과 LLM 판정 모두 이 결과를 사용한다.
"""

import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

import tldextract

# 인터넷에서 공개 접미사 목록을 내려받지 않고 패키지에 내장된 목록만 사용
_extract = tldextract.TLDExtract(suffix_list_urls=())

BRANDS_PATH = Path(__file__).resolve().parent.parent / "data" / "brands.json"

# 단축 URL 서비스 목록 (필요하면 추가)
SHORTENER_DOMAINS = {
    "bit.ly", "han.gl", "me2.do", "t.ly", "tinyurl.com", "goo.gl", "ow.ly",
    "is.gd", "buly.kr", "vo.la", "url.kr", "naver.me", "t.co", "rebrand.ly",
    "cutt.ly", "shorturl.at", "tiny.cc", "c11.kr", "abit.ly", "lrl.kr",
}

# 저가·악용이 잦은 TLD 목록 (필요하면 추가)
CHEAP_TLDS = {
    "xyz", "top", "click", "icu", "buzz", "monster", "rest", "cfd", "sbs",
    "cyou", "lol", "quest", "beauty", "hair", "skin", "bond", "shop", "online",
    "site", "live", "work", "tk", "ml", "ga", "cf", "gq",
}

LOOKALIKE_MAX_DISTANCE = 2   # 편집 거리 1~2 를 유사 도메인으로 본다
LOOKALIKE_MIN_LENGTH = 5     # 너무 짧은 이름은 우연히 비슷할 수 있어 제외


def load_brands(path: Path | str = BRANDS_PATH) -> list:
    """brands.json 을 읽는다. 없거나 깨졌으면 빈 목록."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def levenshtein(a: str, b: str) -> int:
    """두 문자열의 편집 거리 (삽입·삭제·치환 횟수의 최솟값)."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_official(host: str, domains: list) -> bool:
    """host 가 공식 도메인 자체이거나 그 하위 도메인인가."""
    return any(host == d or host.endswith("." + d) for d in domains)


def find_lookalike(host: str, registered_domain: str, brands: list) -> str | None:
    """유사 도메인이면 사칭 대상 브랜드명을 돌려준다."""
    if not host:
        return None
    label = registered_domain.split(".")[0] if registered_domain else ""
    for brand in brands:
        domains = [d.lower() for d in brand.get("domains", [])]
        if _is_official(host, domains):
            continue  # 공식 도메인이면 사칭 아님
        # (1) 브랜드 영문 키워드가 주소 안에 있는데 공식 도메인이 아님
        for kw in brand.get("keywords", []):
            if kw and kw.lower() in host:
                return brand.get("name")
        # (2) 공식 도메인 이름과 편집 거리 1~2
        for d in domains:
            brand_label = d.split(".")[0]
            if len(label) >= LOOKALIKE_MIN_LENGTH and len(brand_label) >= LOOKALIKE_MIN_LENGTH:
                if 1 <= levenshtein(label, brand_label) <= LOOKALIKE_MAX_DISTANCE:
                    return brand.get("name")
    return None


def extract_features(url: str, brands: list | None = None) -> dict:
    """URL 하나의 특징을 뽑는다. 실패해도 예외 대신 error 필드를 채운다."""
    if brands is None:
        brands = load_brands()
    feats = {
        "url": url,
        "host": None,
        "registered_domain": None,
        "subdomain": None,
        "tld": None,
        "is_https": False,
        "is_ip": False,
        "has_punycode": False,
        "subdomain_depth": 0,
        "url_length": len(url or ""),
        "has_at": False,
        "is_shortener": False,
        "is_cheap_tld": False,
        "is_apk": False,
        "lookalike_of": None,
        "error": None,
    }
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        feats["host"] = host
        feats["is_https"] = parsed.scheme.lower() == "https"
        # '@' 는 사용자명 위장(https://bank.com@evil.com) 에 쓰인다
        feats["has_at"] = "@" in (parsed.netloc or "")
        feats["has_punycode"] = "xn--" in host
        feats["is_apk"] = parsed.path.lower().endswith(".apk")

        try:
            ipaddress.ip_address(host)
            feats["is_ip"] = True
        except ValueError:
            pass

        ext = _extract(host)
        feats["tld"] = ext.suffix or None
        feats["subdomain"] = ext.subdomain or ""
        feats["registered_domain"] = (
            f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else host
        )
        feats["subdomain_depth"] = len(ext.subdomain.split(".")) if ext.subdomain else 0

        # 저가 TLD 는 마지막 조각 기준 (예: co.kr → kr)
        last_label = (ext.suffix or "").split(".")[-1]
        feats["is_cheap_tld"] = last_label in CHEAP_TLDS
        feats["is_shortener"] = feats["registered_domain"] in SHORTENER_DOMAINS or host in SHORTENER_DOMAINS

        if not feats["is_ip"]:
            feats["lookalike_of"] = find_lookalike(host, feats["registered_domain"], brands)
    except Exception as e:
        feats["error"] = f"URL 특징 추출 오류: {e}"
    return feats
