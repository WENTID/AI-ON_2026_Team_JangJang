"""분석에 필요한 정보를 한 번에 모아 '스냅샷'(dict)으로 만든다.

실시간 분석과 검증용 저장본이 같은 형식을 쓰므로, 분석 로직(analyzer)은 둘을 구분하지 않는다.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import lookups
from .fetcher import fetch
from .urls import hostname, registrable

SNAPSHOT_VERSION = 1


def collect(url: str, *, source: str = "live", label: str | None = None) -> dict:
    trace = fetch(url)
    html = trace.pop("html")
    urls = [hop["url"] for hop in trace["chain"]] or [url]
    final_host = hostname(trace["final_url"])
    final_https = urlsplit(trace["final_url"]).scheme == "https"

    # 서로 관계없는 조회는 동시에 실행해 기다리는 시간을 줄인다.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_domain = pool.submit(lookups.lookup_domain, registrable(final_host))
        f_cert = pool.submit(lookups.check_cert, final_host) if final_https and not trace["error"] else None
        f_sb = pool.submit(lookups.safe_browsing, urls)
        f_feed = pool.submit(lookups.phish_feed, urls)
        domain = f_domain.result()
        cert = f_cert.result() if f_cert else {"checked": False, "valid": None, "kind": None,
                                               "error": "https 아님" if not final_https else "접속 실패"}
        sb = f_sb.result()
        feed = f_feed.result()

    return {
        "version": SNAPSHOT_VERSION,
        "url": url,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "label": label,
        "trace": trace,
        "html": html,
        "domain": domain,
        "cert": cert,
        "safe_browsing": sb,
        "phish_feed": feed,
    }
