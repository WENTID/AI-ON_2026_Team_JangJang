"""브랜드 목록(config/brands.json)을 이용한 판별: 유사 도메인(타이포스쿼팅), 페이지 속 브랜드 언급."""
import re

from . import config
from .urls import domain_label, host_matches, is_ip, registrable, subdomain

# 눈으로 구분하기 어려운 문자 바꿔치기 (예: paypa1 → paypal, rn → m)
_HOMOGLYPHS = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("3", "e"), ("5", "s"), ("@", "a")]


def _normalize(label: str) -> str:
    label = label.lower()
    for a, b in _HOMOGLYPHS:
        label = label.replace(a, b)
    return label


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def official_brand(host: str) -> dict | None:
    """host 가 어떤 브랜드의 공식 도메인이면 그 브랜드를 돌려준다."""
    for brand in config.brands():
        if host_matches(host, brand["official_domains"]):
            return brand
    return None


def find_lookalike(host: str) -> dict | None:
    """host 가 브랜드 공식 도메인과 비슷하지만 공식이 아니면 {"brand", "official", "how"} 를 돌려준다."""
    if not host or is_ip(host) or official_brand(host):
        return None
    label = domain_label(host)
    norm = _normalize(label)
    tokens = set(re.split(r"[-_.0-9]+", label)) | set(re.split(r"[-_.]+", subdomain(host)))
    reg = registrable(host)
    for brand in config.brands():
        official = brand["official_domains"][0]
        for kw in brand.get("keywords", []):
            kw = kw.lower()
            if len(kw) < 5:
                continue
            if label == kw:
                # 같은 이름에 다른 최상위 도메인 (예: naver.co, kbstar.net)
                return {"brand": brand, "official": official, "how": f"{official}과 이름이 같지만 다른 주소({reg})"}
            if norm == kw or _edit_distance(label, kw) <= (1 if len(kw) < 8 else 2):
                return {"brand": brand, "official": official, "how": f"{official}과 비슷한 도메인({reg})"}
            if kw in tokens:
                return {"brand": brand, "official": official, "how": f"주소에 '{kw}'가 들어 있지만 공식 도메인({official})이 아님"}
            if len(kw) >= 6 and kw in label:
                return {"brand": brand, "official": official, "how": f"도메인에 '{kw}'를 포함하지만 공식 도메인({official})이 아님"}
    return None


def _alias_pattern(alias: str) -> re.Pattern:
    if alias.isascii():
        return re.compile(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])")
    return re.compile(re.escape(alias.lower()))


def brands_in_text(text: str) -> list[dict]:
    """텍스트에 이름·별칭이 나오는 브랜드 목록 (등장 순서대로)."""
    text = (text or "").lower()
    found = []
    for brand in config.brands():
        names = [brand["name"], *brand.get("aliases", [])]
        positions = [m.start() for n in names if len(n) >= 2 for m in [_alias_pattern(n).search(text)] if m]
        if positions:
            found.append((min(positions), brand))
    return [b for _, b in sorted(found, key=lambda x: x[0])]


def find_brand(name: str | None) -> dict | None:
    """AI가 말한 서비스 이름을 브랜드 목록에서 찾는다."""
    if not name:
        return None
    name = name.strip().lower()
    for brand in config.brands():
        for n in [brand["name"], *brand.get("aliases", [])]:
            if n.lower() == name or (len(n) >= 3 and (n.lower() in name or name in n.lower())):
                return brand
    return None
