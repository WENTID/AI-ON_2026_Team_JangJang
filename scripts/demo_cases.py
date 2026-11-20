"""시연용 사례: 'URL만 보면 안전(정황 미약)인데, QR 안내문과 비교하면 목적이 달라 경고'인 경우를 재현한다.

가짜 페이지는 demo_site/*.html 이고, 서버의 /demo-site/<이름> 주소로 열린다. (폼 전송 없음, 검색 노출 차단)
안내문 포스터(QR 포함)는 demo/case*.png 로 만든다. QR에는 배포 주소(--base)의 가짜 페이지 주소가 들어간다.

사용법
  python scripts/demo_cases.py make  --base https://<Render 주소>      # 포스터 만들기 → demo/
  python scripts/demo_cases.py check --base https://<Render 주소>      # 배포 서버로 실제 리허설 (사진 올리기 → 분석)
  python scripts/demo_cases.py check --local                            # 배포 전 로컬 확인 (페이지 HTML을 직접 넣고 Gemini·Jev는 실제 호출)
필요: requirements-dev.txt (Pillow, 포스터 만들 때만)
"""
import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_BASE = "https://qr-phishing-analyzer.onrender.com"
OUT = ROOT / "demo"

# (번호, 안내문 id(eval/ocr_cases.json), 가짜 페이지, 스티커, 설명, 기대 결과)
CASES = [
    (1, "parking", "event", False, "주차요금 안내문 → 경품 이벤트 응모(이름·휴대폰·주소)", "경고"),
    (2, "food", "invest", False, "식당 테이블 주문 안내문 → 주식 정보 오픈채팅방", "경고"),
    (3, "parcel", "signup", False, "택배 보관함 안내문 → 쇼핑 회원가입(이름·생년월일·휴대폰)", "경고"),
    (4, "parking", "parking", False, "(대조군) 주차요금 안내문 → 주차요금 정산 페이지", "정황 미약 (그대로)"),
    (5, "parking", "event", True, "주차요금 안내문 + QR 위 덧붙인 스티커 → 경품 이벤트", "위험"),
]


def poster_path(n: int, notice: str, page: str, sticker: bool) -> Path:
    return OUT / f"case{n}_{notice}_{page}{'_sticker' if sticker else ''}.png"


def cmd_make(base: str) -> None:
    import ocr_test
    from PIL import Image
    OUT.mkdir(exist_ok=True)
    for n, notice_id, page, sticker, desc, _ in CASES:
        case = next(c for c in ocr_test.CASES["cases"] if c["id"] == notice_id)
        url = f"{base.rstrip('/')}/demo-site/{page}"
        if sticker:
            # 원래 안내문에는 정상 주소 QR이 인쇄돼 있고, 그 위에 가짜 페이지 QR 스티커를 덧붙인 모습
            original = {**case, "qr": f"{base.rstrip('/')}/demo-site/parking"}
            img = ocr_test.make_poster(original)
            img = _stick(img, url, len(case["lines"]))
        else:
            img = ocr_test.make_poster({**case, "qr": url})
        path = poster_path(n, notice_id, page, sticker)
        img.save(path)
        print(f"사례 {n}: {path.name}  ({desc})  QR → {url}")
    (OUT / "demo_cases.json").write_text(json.dumps({"base": base}, ensure_ascii=False), encoding="utf-8")


def _stick(img, url: str, n_lines: int):
    """ocr_test 의 스티커 조건과 같은 방식으로, 원래 QR 위에 다른 QR 스티커를 비뚤게 붙인다."""
    import ocr_test
    from PIL import Image
    out = img.copy()
    sticker = Image.new("RGB", (330, 330), (255, 255, 255))
    sticker.paste(ocr_test._qr_image(url, 300), (15, 15))
    sticker = sticker.rotate(-4, expand=True, fillcolor=(0, 0, 0))
    mask = Image.new("L", (330, 330), 255).rotate(-4, expand=True, fillcolor=0)
    x, y = (img.width - 360) // 2 + 30, 200 + 62 * n_lines + 40
    out.paste(Image.new("RGB", sticker.size, (90, 90, 90)), (x + 8, y + 8), mask)
    out.paste(sticker, (x, y), mask)
    return out


def _show(n, desc, expect, plain, ctx):
    c = ctx["context"]
    print(f"\n[사례 {n}] {desc}")
    print(f"  URL만 검사     : {plain['summary']} ({plain['score']}점)")
    print(f"  안내문과 비교  : {ctx['summary']} ({ctx['score']}점)   기대: {expect}")
    print(f"  안내문 목적 '{c['notice_purpose']}' vs 페이지 목적 '{c['page_purpose']}' → {c['decision']}"
          f" (불일치 확률 {c['p_mismatch']}, {c['engine']})"
          + (" · 스티커 의심" if c["notice"] and c["notice"]["sticker"] else ""))


def cmd_check_remote(base: str) -> None:
    import httpx
    for n, notice_id, page, sticker, desc, expect in CASES:
        path = poster_path(n, notice_id, page, sticker)
        with httpx.Client(base_url=base, timeout=90) as cl:
            up = cl.post("/api/image", files={"file": (path.name, path.read_bytes(), "image/png")},
                         data={"decode": "true"}).json()
            urls = [c["text"] for c in up["codes"] if c["is_url"]]
            if not urls:
                print(f"[사례 {n}] 포스터에서 QR을 읽지 못함")
                continue
            plain = cl.post("/api/analyze", json={"url": urls[0]}).json()
            ctx = cl.post("/api/analyze", json={"url": urls[0], "image_id": up["image_id"]}).json()
        _show(n, desc, expect, plain, ctx)


def cmd_check_local(base: str) -> None:
    """배포 전 확인: 가짜 페이지 HTML을 직접 저장본으로 만들어 분석한다. (접속·인증서·도메인 조회만 흉내, AI는 실제 호출)"""
    from app.analyzer import analyze
    from app.uploads import UPLOAD_DIR
    from tests.test_rules import fake_snapshot
    for n, notice_id, page, sticker, desc, expect in CASES:
        html = (ROOT / "demo_site" / f"{page}.html").read_text(encoding="utf-8")
        snap = fake_snapshot(f"{base.rstrip('/')}/demo-site/{page}", html, created="2019-03-01T00:00:00Z")
        snap["cert"] = {"checked": True, "valid": True, "kind": None, "error": None}
        image_id = uuid.uuid4().hex
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        import cv2
        img = cv2.imread(str(poster_path(n, notice_id, page, sticker)))
        cv2.imwrite(str(UPLOAD_DIR / f"{image_id}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        plain = analyze(snap, ai_wait=True)
        ctx = analyze(snap, ai_wait=True, context_image_id=image_id)
        _show(n, desc, expect, plain, ctx)


def main() -> None:
    ap = argparse.ArgumentParser(description="시연용 사례 만들기·리허설")
    ap.add_argument("cmd", choices=["make", "check"])
    ap.add_argument("--base", default=None, help="배포 주소 (예: https://qr-phishing-analyzer.onrender.com)")
    ap.add_argument("--local", action="store_true", help="배포 없이 로컬에서 확인")
    a = ap.parse_args()
    saved = OUT / "demo_cases.json"
    base = a.base or (json.loads(saved.read_text(encoding="utf-8"))["base"] if saved.exists() else DEFAULT_BASE)
    if a.cmd == "make":
        cmd_make(base)
    elif a.local:
        cmd_check_local(base)
    else:
        cmd_check_remote(base)


if __name__ == "__main__":
    main()
