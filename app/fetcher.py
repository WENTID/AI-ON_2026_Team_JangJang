"""분석 대상 페이지에 안전하게 접속해 리다이렉트 경로, 응답 헤더, HTML을 가져온다.

안전 규칙
- 리다이렉트를 한 단계씩 직접 따라가며 기록한다. (횟수 제한)
- 파일(앱 설치 파일 등)은 헤더만 보고 본문을 내려받지 않는다.
- HTML도 정해진 크기까지만 읽는다. 전체 접속 시간에 제한을 둔다.
- 내부망 주소(127.0.0.1, 192.168.x.x 등)로는 접속하지 않는다. (서버를 악용한 내부망 접근 방지)
- 폼 제출, 스크립트 실행 같은 상호작용은 하지 않는다. (GET 요청만)
"""
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx

from . import config
from .urls import hostname

_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']?\s*\d*\s*;?\s*url\s*=\s*([^"'>\s]+)""",
    re.IGNORECASE,
)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([a-zA-Z0-9_\-]+)""", re.IGNORECASE)
_KEEP_HEADERS = {"content-type", "content-disposition", "content-length", "server", "location",
                 "strict-transport-security", "x-powered-by", "refresh"}


class BlockedHost(Exception):
    pass


def check_host_allowed(host: str, allow_private: bool) -> None:
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ConnectionError(f"도메인을 찾을 수 없음 ({host})") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise BlockedHost(f"내부망 주소로는 접속하지 않음 ({host} → {ip})")


def is_download(url: str, headers: dict) -> bool:
    cfg = config.settings()
    path = urlsplit(url).path.lower()
    if any(path.endswith(ext) for ext in cfg["download_extensions"]):
        return True
    ctype = headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype in cfg["download_content_types"]:
        return True
    disposition = headers.get("content-disposition", "").lower()
    return "attachment" in disposition


def _decode(body: bytes, declared: str | None) -> str:
    candidates = []
    if declared:
        candidates.append(declared)
    m = _META_CHARSET.search(body[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp949"]
    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def fetch(url: str) -> dict:
    """url 에 접속한 결과를 dict 로 돌려준다. 실패해도 예외 대신 error 필드에 사유를 담는다."""
    cfg = config.settings()["fetch"]
    result = {
        "input_url": url,
        "chain": [],          # [{"url":..., "status":...}] 리다이렉트 경로
        "final_url": url,
        "status": None,
        "headers": {},
        "content_type": "",
        "is_download": False,
        "html": "",
        "html_truncated": False,
        "error": None,
    }
    deadline = time.monotonic() + cfg["timeout_sec"] * 2
    current = url
    refreshes = 0
    try:
        with httpx.Client(
            verify=False,  # 인증서 문제는 cert 모듈에서 따로 검사한다. 여기서는 페이지 내용을 받아오는 게 목적.
            follow_redirects=False,
            timeout=cfg["timeout_sec"],
            headers={"User-Agent": cfg["user_agent"], "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
        ) as client:
            for _ in range(cfg["max_redirects"] + 1):
                if time.monotonic() > deadline:
                    raise TimeoutError("접속 시간 초과")
                check_host_allowed(hostname(current), cfg["allow_private_hosts"])
                with client.stream("GET", current) as resp:
                    result["chain"].append({"url": current, "status": resp.status_code})
                    location = resp.headers.get("location")
                    if resp.is_redirect and location:
                        current = urljoin(current, location)
                        continue

                    headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() in _KEEP_HEADERS}
                    result.update(final_url=current, status=resp.status_code, headers=headers,
                                  content_type=headers.get("content-type", ""))
                    if is_download(current, headers):
                        result["is_download"] = True  # 본문은 읽지 않고 연결을 닫는다.
                        break

                    ctype = result["content_type"].lower()
                    if ctype and "html" not in ctype and "text" not in ctype and "xml" not in ctype:
                        break  # 이미지·PDF 등 HTML이 아닌 응답은 본문을 읽지 않는다.

                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) >= cfg["max_html_bytes"]:
                            result["html_truncated"] = True
                            break
                        if time.monotonic() > deadline:
                            raise TimeoutError("접속 시간 초과")
                    html = _decode(bytes(body[: cfg["max_html_bytes"]]), resp.charset_encoding)
                    result["html"] = html

                # <meta http-equiv="refresh"> 로 넘기는 페이지는 정해진 횟수만큼 따라간다.
                m = _META_REFRESH.search(result["html"][:20000])
                if m and refreshes < cfg["max_meta_refresh"]:
                    refreshes += 1
                    nxt = urljoin(current, m.group(1).strip())
                    if nxt != current and urlsplit(nxt).scheme in ("http", "https"):
                        current = nxt
                        result["html"] = ""
                        continue
                break
            else:
                result["error"] = "리다이렉트가 너무 많음"
    except BlockedHost as e:
        result["error"] = str(e)
    except (httpx.TimeoutException, TimeoutError):
        result["error"] = "접속 시간 초과"
    except ConnectionError as e:
        result["error"] = str(e)
    except httpx.HTTPError as e:
        result["error"] = f"접속 실패 ({type(e).__name__})"
    return result
