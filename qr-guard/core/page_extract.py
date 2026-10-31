"""페이지 텍스트·요구 정보 추출.

최종 페이지 HTML 에서 제목, 본문 요약, form, 입력란, 요구 정보 종류,
앱 설치 유도, 압박 문구를 뽑는다. 스크립트는 실행하지 않는다.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from core.url_features import _extract

TEXT_SUMMARY_CHARS = 1500

# 입력란 분류 규칙: 위에서부터 차례로 검사해 처음 맞는 범주를 쓴다.
# (순서 중요: "카드 비밀번호"는 login 보다 card_password 가 먼저 잡혀야 한다)
CATEGORY_RULES = [
    ("card_cvc", ["cvc", "cvv", "보안코드", "security code"]),
    ("card_password", ["카드 비밀번호", "카드비밀번호", "앞 2자리", "앞2자리", "앞 두자리",
                       "card_pw", "cardpw", "card_password", "cardpass", "card-pw"]),
    ("card_number", ["카드번호", "카드 번호", "cardno", "card_no", "cardnum",
                     "card_number", "card-number", "유효기간", "expiry", "card"]),
    ("resident_id", ["주민등록번호", "주민번호", "주민", "뒷자리", "resident", "jumin", "ssn", "rrn"]),
    ("bank_account", ["계좌", "은행", "account", "bank"]),
    ("otp_cert", ["otp", "인증번호", "공동인증서", "인증서", "보안카드", "verification", "auth_code", "authcode"]),
    ("login", ["아이디", "비밀번호", "password", "passwd", "userid", "user_id", "username",
               "login", "로그인"]),
    ("phone", ["휴대폰", "휴대전화", "핸드폰", "전화번호", "연락처", "phone", "mobile", "tel"]),
    ("name_address", ["이름", "성명", "주소", "name", "address", "addr", "우편번호", "zipcode"]),
    ("vehicle", ["차량번호", "차량", "vehicle", "car_no", "carno", "car_num", "plate"]),
]

# 사용자 입력이 아닌 input 타입은 요구 정보 분류에서 뺀다
NON_DATA_INPUT_TYPES = {"hidden", "submit", "button", "image", "reset", "checkbox", "radio"}

APP_INSTALL_PHRASES = ["앱 설치", "앱을 설치", "어플 설치", "어플리케이션 설치", "보안 프로그램",
                       "보안프로그램", "보안 앱", "설치 파일", "apk", "원격", "팀뷰어", "애니데스크"]

URGENCY_PHRASES = ["과태료", "미납", "즉시", "정지", "오늘까지", "금일", "압류", "법적 조치",
                   "연체", "마감", "긴급", "차단", "불이익", "기한 내", "독촉"]
URGENCY_REGEX = re.compile(r"\d+\s*(?:분|시간)\s*(?:이내|내)")


def _same_site(url_a: str, url_b: str) -> bool:
    """두 URL 의 등록 도메인이 같은가."""
    def reg(u):
        ext = _extract(urlparse(u).hostname or "")
        return f"{ext.domain}.{ext.suffix}".lower()
    return reg(url_a) == reg(url_b)


def classify_input(text: str, input_type: str = "") -> str:
    """입력란 설명 텍스트(이름·id·placeholder·라벨)로 범주를 정한다."""
    t = text.lower()
    for category, keywords in CATEGORY_RULES:
        if any(kw.lower() in t for kw in keywords):
            return category
    if input_type == "password":
        return "login"
    if input_type == "tel":
        return "phone"
    return "other"


def _label_text(soup, el) -> str:
    """input 에 연결된 label 글자를 찾는다."""
    el_id = el.get("id")
    if el_id:
        lab = soup.find("label", attrs={"for": el_id})
        if lab:
            return lab.get_text(" ", strip=True)
    parent_label = el.find_parent("label")
    if parent_label:
        return parent_label.get_text(" ", strip=True)
    return ""


def _empty_page() -> dict:
    return {
        "title": "",
        "text_summary": "",
        "forms": [],
        "inputs": [],
        "requested_info": [],
        "app_install_links": [],
        "urgency_phrases": [],
        "error": None,
    }


def extract_page(html: str, final_url: str) -> dict:
    """HTML 에서 판정에 필요한 정보를 뽑는다."""
    page = _empty_page()
    if not html:
        page["error"] = "페이지 HTML 이 없습니다."
        return page
    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1) 제목
        page["title"] = soup.title.get_text(strip=True) if soup.title else ""

        # 2) 보이는 텍스트 (script/style/주석 제외)
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        body = soup.body or soup
        text = re.sub(r"\s+", " ", body.get_text(" ", strip=True))
        page["text_summary"] = text[:TEXT_SUMMARY_CHARS]

        # 3) form 과 전송 대상
        for form in soup.find_all("form"):
            action = (form.get("action") or "").strip()
            if not action or action.startswith("#") or action.lower().startswith("javascript:"):
                resolved, same = final_url, True
            else:
                resolved = urljoin(final_url, action)
                same = _same_site(resolved, final_url)
            page["forms"].append({
                "action": action or "(없음)",
                "action_url": resolved,
                "method": (form.get("method") or "get").lower(),
                "same_domain": same,
            })

        # 4) 입력란
        categories = []
        for el in soup.find_all(["input", "select", "textarea"]):
            itype = (el.get("type") or el.name).lower() if el.name == "input" else el.name
            info = {
                "type": itype,
                "name": el.get("name", ""),
                "id": el.get("id", ""),
                "placeholder": el.get("placeholder", ""),
                "label": _label_text(soup, el),
            }
            if itype in NON_DATA_INPUT_TYPES:
                info["category"] = None
            else:
                desc = " ".join([info["name"], info["id"], info["placeholder"], info["label"]])
                info["category"] = classify_input(desc, itype)
                if info["category"] not in categories:
                    categories.append(info["category"])
            page["inputs"].append(info)
        page["requested_info"] = categories

        # 5) 앱 설치 유도
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if urlparse(urljoin(final_url, href)).path.lower().endswith(".apk"):
                found.append(f"APK 링크: {urljoin(final_url, href)}")
        lower_text = text.lower()
        for phrase in APP_INSTALL_PHRASES:
            if phrase.lower() in lower_text:
                found.append(f"문구: {phrase}")
        page["app_install_links"] = found

        # 6) 압박 문구
        urgent = [p for p in URGENCY_PHRASES if p in text]
        urgent += [m.group(0) for m in URGENCY_REGEX.finditer(text)]
        page["urgency_phrases"] = list(dict.fromkeys(urgent))  # 중복 제거, 순서 유지

    except Exception as e:
        page["error"] = f"페이지 분석 오류: {e}"
    return page
