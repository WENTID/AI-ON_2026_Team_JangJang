"""URL·도메인 문자열을 다루는 공통 함수."""
import ipaddress
from urllib.parse import urlsplit

import tldextract

# include_psl_private_domains: github.io, web.app 같은 무료 호스팅 주소를 사용자별 도메인으로 구분한다.
# (abc.github.io 와 xyz.github.io 를 같은 사이트로 보지 않기 위해)
_extract = tldextract.TLDExtract(include_psl_private_domains=True)


class InvalidUrl(ValueError):
    pass


def normalize_input(raw: str) -> str:
    """사용자가 입력한 문자열을 http(s) URL로 정리한다. 스킴이 없으면 https 로 가정."""
    text = (raw or "").strip()
    if not text:
        raise InvalidUrl("URL이 비어 있습니다.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidUrl("http 또는 https 주소만 분석할 수 있습니다.")
    if not parts.hostname:
        raise InvalidUrl("올바른 URL 형식이 아닙니다.")
    return text


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def registrable(host: str) -> str:
    """naver.com, abc.github.io 처럼 '등록된 도메인' 부분만 돌려준다. IP면 그대로."""
    host = host.lower().rstrip(".")
    if not host or is_ip(host):
        return host
    ext = _extract(host)
    return ext.top_domain_under_public_suffix or host


def domain_label(host: str) -> str:
    """kbstar.com → kbstar, naver.co.kr → naver"""
    host = host.lower().rstrip(".")
    if is_ip(host):
        return host
    return _extract(host).domain


def suffix(host: str) -> str:
    return _extract(host.lower()).suffix


def subdomain(host: str) -> str:
    return _extract(host.lower()).subdomain


def host_matches(host: str, entries) -> bool:
    """host 가 목록의 도메인과 같거나 그 하위 도메인이면 True."""
    host = host.lower().rstrip(".")
    for entry in entries:
        entry = entry.lower()
        if host == entry or host.endswith("." + entry):
            return True
    return False
