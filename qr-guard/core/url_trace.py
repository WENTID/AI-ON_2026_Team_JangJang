"""리다이렉트 추적.

httpx 의 자동 리다이렉트를 끄고(follow_redirects=False), Location 헤더를
한 단계씩 직접 따라간다. HTML 의 meta refresh 와 단순한 location.href
스크립트 리다이렉트도 감지한다.

안전 규칙
  - GET 요청만 보낸다. form 제출·스크립트 실행·파일 다운로드는 하지 않는다.
  - APK 는 본문을 내려받지 않고 is_download=True 로만 표시한다.
  - 테스트 모드에서는 override 에 없는 URL 에 절대 접속하지 않는다.

테스트 모드 (override)
  override = {"표시용 URL": "실제 접속할 로컬 URL"}
  요청은 로컬 가짜 서버로 보내되, chain/final_url 에는 표시용 URL 을 기록한다.
  덕분에 실제 피싱 사이트 없이도 "수상한 도메인"을 재현할 수 있다.
"""

import re
from urllib.parse import urljoin, urlparse

import httpx

MAX_HOPS = 10                 # 최대 리다이렉트 횟수
TIMEOUT_SEC = 5.0             # 요청당 타임아웃
MAX_BODY_BYTES = 1_000_000    # 요청당 본문 최대 1MB
APK_CONTENT_TYPE = "application/vnd.android.package-archive"
USER_AGENT = "Mozilla/5.0 (Linux; Android 14) qr-guard-checker/0.1"

# <meta http-equiv="refresh" content="0; url=...">
_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']\s*\d*\s*;?\s*url\s*=\s*([^"'>]+)""",
    re.IGNORECASE,
)
# location.href = "..." / window.location = '...' / location.replace("...")
_JS_LOCATION_RE = re.compile(
    r"""(?:window\.|document\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']"""
    r"""|location\.replace\(\s*["']([^"']+)["']\s*\)""",
    re.IGNORECASE,
)


def _is_apk_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".apk")


def _lookup_override(url: str, override: dict | None) -> str | None:
    """표시용 URL → 실제 접속 URL. 끝의 '/' 차이는 무시한다."""
    if not override:
        return None
    if url in override:
        return override[url]
    stripped = url.rstrip("/")
    for k, v in override.items():
        if k.rstrip("/") == stripped:
            return v
    return None


def find_client_redirect(html: str) -> str | None:
    """HTML 안의 meta refresh 또는 location.href 리다이렉트 대상을 찾는다."""
    m = _META_REFRESH_RE.search(html)
    if m:
        return m.group(1).strip()
    m = _JS_LOCATION_RE.search(html)
    if m:
        return (m.group(1) or m.group(2)).strip()
    return None


def _empty_result() -> dict:
    return {
        "chain": [],            # [{"url", "status", "via"}]
        "final_url": None,
        "final_status": None,
        "content_type": None,
        "is_download": False,
        "html": None,           # 최종 페이지 HTML (다운로드면 None)
        "last_html": None,      # 지나온 HTML 중 마지막 것 (APK 유도 페이지 분석용)
        "last_html_url": None,
        "error": None,
    }


def trace(url: str, test_mode: bool = False, override: dict | None = None) -> dict:
    """URL 을 최종 목적지까지 따라간다.

    반환: {"chain", "final_url", "final_status", "content_type",
           "is_download", "html", "last_html", "last_html_url", "error"}
    """
    result = _empty_result()
    current = (url or "").strip()
    via = "start"

    if not current:
        result["error"] = "URL 이 비어 있습니다."
        return result

    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=TIMEOUT_SEC,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for _hop in range(MAX_HOPS + 1):
                scheme = urlparse(current).scheme.lower()
                if scheme not in ("http", "https"):
                    result["error"] = f"지원하지 않는 주소 형식입니다: {scheme or '(없음)'}"
                    break

                result["final_url"] = current

                # 1) URL 이 .apk 로 끝나면 요청하지 않고 다운로드로 표시
                if _is_apk_url(current):
                    result["chain"].append({"url": current, "status": None, "via": via})
                    result["is_download"] = True
                    result["content_type"] = APK_CONTENT_TYPE
                    break

                # 2) 실제 접속할 주소 결정
                real_url = current
                if test_mode:
                    real_url = _lookup_override(current, override)
                    if real_url is None:
                        # 테스트 모드에서는 실제 인터넷에 접속하지 않는다
                        result["chain"].append({"url": current, "status": None, "via": via})
                        result["error"] = f"테스트 모드: override 에 없는 주소라 접속하지 않았습니다 ({current})"
                        break

                # 3) GET 요청 (본문은 스트리밍으로 최대 1MB 까지만)
                with client.stream("GET", real_url) as resp:
                    status = resp.status_code
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    result["chain"].append({"url": current, "status": status, "via": via})
                    result["final_status"] = status
                    result["content_type"] = ctype

                    # 3-a) HTTP 리다이렉트
                    location = resp.headers.get("location")
                    if 300 <= status < 400 and location:
                        current = urljoin(current, location)
                        via = "http"
                        continue

                    # 3-b) APK 다운로드 응답 → 본문 받지 않음
                    if ctype == APK_CONTENT_TYPE:
                        result["is_download"] = True
                        break

                    # 3-c) 본문 읽기 (1MB 제한)
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BODY_BYTES:
                            body = body[:MAX_BODY_BYTES]
                            break
                    encoding = resp.encoding or "utf-8"

                text = body.decode(encoding, errors="replace")
                is_html = "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))
                if not is_html:
                    break

                result["html"] = text
                result["last_html"] = text
                result["last_html_url"] = current

                # 3-d) meta refresh / location.href 리다이렉트
                target = find_client_redirect(text)
                if target:
                    next_url = urljoin(current, target)
                    if next_url != current:
                        current = next_url
                        via = "html"
                        result["html"] = None  # 다음 페이지로 넘어가므로 초기화
                        continue
                break
            else:
                result["error"] = f"리다이렉트가 {MAX_HOPS}번을 넘어 추적을 멈췄습니다."

    except httpx.TimeoutException:
        result["error"] = f"응답 시간 초과 ({TIMEOUT_SEC:.0f}초)"
    except httpx.ConnectError:
        hint = " (가짜 서버 python mock_site/server.py 가 켜져 있나요?)" if test_mode else ""
        result["error"] = "서버에 연결할 수 없습니다." + hint
    except Exception as e:
        result["error"] = f"주소 추적 중 오류: {e}"

    return result
