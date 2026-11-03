"""HTML에서 사칭 판별에 필요한 정보(제목, 본문, 입력 폼, 이미지, 링크 도메인)를 뽑아낸다.

헤드리스 브라우저 없이 HTML 텍스트만으로 동작한다. (요구사항 6-1 ①)
"""
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .urls import hostname, registrable

# 입력란의 name/id/placeholder/라벨에 이런 단어가 있으면 해당 종류의 민감 정보로 본다.
SENSITIVE_KEYWORDS = {
    "비밀번호": ["password", "passwd", "pwd", "비밀번호", "패스워드"],
    "카드번호": ["card", "cardno", "cc-number", "ccnum", "카드번호", "카드 번호"],
    "CVC": ["cvc", "cvv", "csc", "security code", "보안코드"],
    "카드 유효기간": ["expiry", "exp-date", "expdate", "cc-exp", "유효기간"],
    "인증번호/OTP": ["otp", "인증번호", "auth code", "verification code", "verify code", "sms code", "보안카드"],
    "주민등록번호": ["ssn", "jumin", "resident", "주민등록", "주민번호"],
    "계좌번호": ["account number", "acctno", "bank account", "계좌번호", "계좌"],
}
SENSITIVE_KINDS = set(SENSITIVE_KEYWORDS)
OTHER_KEYWORDS = {
    "아이디/이메일": ["userid", "user_id", "username", "login", "email", "e-mail", "아이디", "이메일"],
    "휴대폰번호": ["phone", "mobile", "휴대폰", "전화번호", "핸드폰"],
    "이름": ["name", "이름", "성명"],
}
_SPACE = re.compile(r"\s+")


def _field_text(tag, soup) -> str:
    parts = [tag.get(a, "") for a in ("name", "id", "placeholder", "aria-label", "autocomplete", "title")]
    if tag.get("id"):
        label = soup.find("label", attrs={"for": tag["id"]})
        if label:
            parts.append(label.get_text(" ", strip=True))
    return " ".join(parts).lower()


def _classify(tag, soup) -> list[str]:
    kinds = []
    input_type = (tag.get("type") or "").lower()
    if input_type in ("hidden", "submit", "button", "checkbox", "radio", "image", "reset"):
        return kinds
    if input_type == "password":
        kinds.append("비밀번호")
    elif input_type == "tel":
        kinds.append("휴대폰번호")
    text = _field_text(tag, soup)
    for kind, words in {**SENSITIVE_KEYWORDS, **OTHER_KEYWORDS}.items():
        if kind not in kinds and any(w in text for w in words):
            kinds.append(kind)
    return kinds


def extract(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    page_host = hostname(base_url)
    page_reg = registrable(page_host)

    title = _SPACE.sub(" ", soup.title.get_text()).strip() if soup.title else ""

    # 입력 폼: <form> 안팎의 모든 입력란을 본다. (자바스크립트로 전송하는 피싱 페이지는 <form> 이 없기도 함)
    forms = []
    for form in soup.find_all("form"):
        action = urljoin(base_url, form.get("action") or base_url)
        forms.append({"action": action, "action_domain": registrable(hostname(action)),
                      "fields": [k for inp in form.find_all(["input", "select", "textarea"]) for k in _classify(inp, soup)]})
    loose = [inp for inp in soup.find_all(["input", "textarea"]) if not inp.find_parent("form")]
    if loose:
        forms.append({"action": None, "action_domain": None, "fields": [k for inp in loose for k in _classify(inp, soup)]})
    requested = sorted({k for f in forms for k in f["fields"]})
    sensitive = sorted(set(requested) & SENSITIVE_KINDS)
    external_actions = sorted({f["action_domain"] for f in forms
                               if f["action_domain"] and f["action_domain"] != page_reg
                               and urlsplit(f["action"]).scheme in ("http", "https")})

    # 이미지 파일명·alt (로고가 이미지로만 있는 경우 단서가 됨), 파비콘
    images = []
    for img in soup.find_all("img")[:30]:
        src = img.get("src") or ""
        name = src.split("?")[0].rstrip("/").rsplit("/", 1)[-1][:60]
        alt = (img.get("alt") or "").strip()[:60]
        if name or alt:
            images.append({"file": name, "alt": alt})
    icon = soup.find("link", rel=lambda v: v and "icon" in (" ".join(v) if isinstance(v, list) else v).lower())
    favicon = urljoin(base_url, icon["href"]) if icon and icon.get("href") else None

    link_domains = Counter()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if urlsplit(href).scheme in ("http", "https"):
            link_domains[registrable(hostname(href))] += 1

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = _SPACE.sub(" ", soup.get_text(" ")).strip()

    return {
        "title": title[:200],
        "text": text,
        "forms": forms,
        "requested_info": requested,
        "sensitive_info": sensitive,
        "external_form_domains": external_actions,
        "images": images,
        "favicon": favicon,
        "link_domains": [d for d, _ in link_domains.most_common(10)],
    }
