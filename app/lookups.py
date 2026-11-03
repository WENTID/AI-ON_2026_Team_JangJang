"""외부 정보 조회: 도메인 등록일(RDAP), 인증서, Google Safe Browsing, 공개 피싱 피드(OpenPhish).

각 함수는 실패해도 예외를 던지지 않고 {"checked": False, "error": "..."} 형태로 돌려준다.
(외부 API가 실패해도 나머지 분석은 계속 진행하기 위해)
"""
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

import httpx

from . import config
from .urls import is_ip


# ---------- 도메인 등록일 (RDAP) ----------
# RDAP: WHOIS 를 대신하는 표준 조회 방식. JSON 으로 응답해 파싱이 쉽다.
# IANA 가 공개하는 안내표(어떤 최상위 도메인을 어느 기관이 관리하는지)를 보고 관리 기관 서버에 직접 묻는다.
_bootstrap_lock = threading.Lock()
_bootstrap: dict[str, str] = {}


def _rdap_base(domain: str) -> str | None:
    with _bootstrap_lock:
        if not _bootstrap:
            cfg = config.settings()["domain_lookup"]
            data = httpx.get(cfg["bootstrap_url"], timeout=15).json()
            for tlds, servers in data["services"]:
                for tld in tlds:
                    _bootstrap[tld.lower()] = servers[0].rstrip("/") + "/"
    return _bootstrap.get(domain.rsplit(".", 1)[-1].lower())


def _rdap_get(url: str, timeout: float) -> httpx.Response:
    for attempt in range(3):
        r = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"Accept": "application/rdap+json"})
        if r.status_code != 429:
            return r
        time.sleep(2 * (attempt + 1))  # 요청이 많다고 거절되면 잠시 기다렸다 다시 시도
    return r


@lru_cache(maxsize=2048)
def lookup_domain(domain: str) -> dict:
    if not domain or is_ip(domain):
        return {"checked": False, "domain": domain, "created": None, "error": "IP 주소라 조회 대상 아님"}
    cfg = config.settings()["domain_lookup"]
    try:
        base = _rdap_base(domain)
        url = f"{base}domain/{domain}" if base else cfg["rdap_url"].format(domain=domain)
        r = _rdap_get(url, cfg["timeout_sec"])
        if r.status_code == 404:
            return {"checked": False, "domain": domain, "created": None, "error": "등록 정보 없음"}
        r.raise_for_status()
        for event in r.json().get("events", []):
            if event.get("eventAction") == "registration":
                return {"checked": True, "domain": domain, "created": event.get("eventDate"), "error": None}
        return {"checked": False, "domain": domain, "created": None, "error": "등록일 정보 없음"}
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 '확인 불가'로 처리
        return {"checked": False, "domain": domain, "created": None, "error": f"조회 실패 ({type(e).__name__})"}


def age_days(created_iso: str | None, at_iso: str | None = None) -> int | None:
    """created 부터 at(기본: 지금)까지 지난 일수. 저장본 분석 시에는 수집 시각을 기준으로 계산한다."""
    if not created_iso:
        return None
    try:
        created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        at = datetime.fromisoformat(at_iso.replace("Z", "+00:00")) if at_iso else datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return (at - created).days
    except ValueError:
        return None


# ---------- 인증서 ----------
_VERIFY_KIND = {10: "expired", 62: "hostname_mismatch", 18: "self_signed", 19: "self_signed", 20: "untrusted"}


def check_cert(host: str, port: int = 443, timeout: float = 5) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        return {"checked": True, "valid": True, "error": None, "kind": None,
                "not_after": cert.get("notAfter"), "issuer": issuer.get("organizationName") or issuer.get("commonName")}
    except ssl.SSLCertVerificationError as e:
        return {"checked": True, "valid": False, "kind": _VERIFY_KIND.get(e.verify_code, "other"),
                "error": e.verify_message or str(e), "not_after": None, "issuer": None}
    except (OSError, ssl.SSLError) as e:
        return {"checked": False, "valid": None, "kind": None, "error": f"인증서 확인 실패 ({type(e).__name__})",
                "not_after": None, "issuer": None}


# ---------- Google Safe Browsing ----------
def safe_browsing(urls: list[str]) -> dict:
    key = config.env("SAFE_BROWSING_API_KEY")
    if not key:
        return {"checked": False, "matches": [], "error": "API 키 없음"}
    cfg = config.settings()["safe_browsing"]
    body = {
        "client": {"clientId": "qr-phishing-analyzer", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": cfg["threat_types"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in dict.fromkeys(urls)],
        },
    }
    try:
        r = httpx.post("https://safebrowsing.googleapis.com/v4/threatMatches:find",
                       params={"key": key}, json=body, timeout=cfg["timeout_sec"])
        r.raise_for_status()
        matches = [{"url": m["threat"]["url"], "threat_type": m["threatType"]} for m in r.json().get("matches", [])]
        return {"checked": True, "matches": matches, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"checked": False, "matches": [], "error": f"조회 실패 ({type(e).__name__})"}


# ---------- OpenPhish 공개 피싱 피드 ----------
# 무료 커뮤니티 피드(https://openphish.com/feed.txt). 12시간마다 갱신되는 최근 피싱 URL 목록.
_feed_lock = threading.Lock()
_feed_cache: dict = {"loaded_at": 0.0, "urls": frozenset()}


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()


def download_feed() -> list[str]:
    cfg = config.settings()["phish_feed"]
    r = httpx.get(cfg["url"], timeout=20, follow_redirects=True)
    r.raise_for_status()
    urls = [line.strip() for line in r.text.splitlines() if line.strip().startswith("http")]
    path = config.DATA_DIR / "feeds" / "openphish.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(urls), encoding="utf-8")
    return urls


def feed_urls() -> frozenset[str]:
    cfg = config.settings()["phish_feed"]
    with _feed_lock:
        if time.time() - _feed_cache["loaded_at"] < cfg["refresh_minutes"] * 60:
            return _feed_cache["urls"]
        path = config.DATA_DIR / "feeds" / "openphish.txt"
        try:
            if path.exists() and time.time() - path.stat().st_mtime < cfg["refresh_minutes"] * 60:
                urls = path.read_text(encoding="utf-8").splitlines()
            else:
                urls = download_feed()
        except Exception:  # noqa: BLE001 - 피드 다운로드 실패 시 예전 파일이라도 사용
            urls = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        _feed_cache.update(loaded_at=time.time(), urls=frozenset(_norm(u) for u in urls))
        return _feed_cache["urls"]


def phish_feed(urls: list[str]) -> dict:
    listed = feed_urls()
    if not listed:
        return {"checked": False, "matches": [], "error": "피드를 불러오지 못함"}
    return {"checked": True, "matches": [u for u in urls if _norm(u) in listed], "error": None}
