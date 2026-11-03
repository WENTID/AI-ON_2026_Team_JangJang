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


# 페이지 제목에서 브랜드를 찾을 때 쓰지 않는 이름: 일반 영어 단어라 브랜드가 아닌 뜻으로 자주 나온다.
_GENERIC_ALIASES = {"line", "steam", "chase", "visa", "x", "booking", "live", "office"}


def _alias_pattern(alias: str) -> re.Pattern:
    alias = alias.lower()
    if alias.isascii():
        return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
    if len(alias) <= 2:
        # '토스', '신협' 같은 짧은 한글 이름은 앞뒤가 다른 한글로 이어지면 다른 단어로 본다. ('토스트' 제외)
        return re.compile(r"(?<![가-힣])" + re.escape(alias) + r"(?![가-힣])")
    return re.compile(re.escape(alias))


def brands_in_text(text: str) -> list[dict]:
    """텍스트에 이름·별칭이 나오는 브랜드 목록 (등장 순서대로)."""
    text = (text or "").lower()
    found = []
    for brand in config.brands():
        names = [n for n in [brand["name"], *brand.get("aliases", [])] if len(n) >= 2 and n.lower() not in _GENERIC_ALIASES]
        positions = [m.start() for n in names for m in [_alias_pattern(n).search(text)] if m]
        if positions:
            found.append((min(positions), brand))
    return [b for _, b in sorted(found, key=lambda x: x[0])]


def _norm_name(s: str) -> str:
    return re.sub(r"[\s\-_.·()/&,]+", "", s.lower())


def find_brand(name: str | None) -> dict | None:
    """AI가 말한 서비스 이름을 브랜드 목록에서 찾는다.
    이름이 정확히 같거나(공백·기호 무시), 4글자 이상 별칭이 단어 단위로 들어 있을 때만 같은 브랜드로 본다.
    ('Groups Inc' 를 UPS로 보는 식의 오인 방지)"""
    if not name:
        return None
    target = _norm_name(name)
    for brand in config.brands():
        if any(_norm_name(n) == target for n in [brand["name"], *brand.get("aliases", [])]):
            return brand
    for brand in config.brands():
        for n in [brand["name"], *brand.get("aliases", [])]:
            if len(n) >= 4 and n.lower() not in _GENERIC_ALIASES and _alias_pattern(n).search(name.lower()):
                return brand
    return None
