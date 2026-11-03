"""시연·테스트용 QR 이미지를 demo/ 폴더에 만든다.

- qr_safe.png    : 정상 사이트(naver.com) QR 1개
- qr_phish.png   : Google Safe Browsing 공식 테스트용 피싱 주소 QR 1개 (실제 피해 없는 테스트 페이지)
- qr_multi.png   : 위 두 QR이 한 이미지에 들어 있는 경우 (여러 QR 선택 기능 확인용)
사용법:  python scripts/make_demo_qr.py
"""
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "demo"
SAFE = "https://www.naver.com/"
PHISH = "http://testsafebrowsing.appspot.com/s/phishing.html"


def qr(text: str, scale: int = 8) -> np.ndarray:
    img = cv2.QRCodeEncoder.create().encode(text)
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)


def poster(text: str, title: str) -> np.ndarray:
    code = qr(text)
    h, w = code.shape
    canvas = np.full((h + 120, max(w, 520)), 255, np.uint8)
    cv2.putText(canvas, title, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    canvas[110:110 + h, (canvas.shape[1] - w) // 2:(canvas.shape[1] - w) // 2 + w] = code
    return canvas


def main() -> None:
    OUT.mkdir(exist_ok=True)
    safe = poster(SAFE, "SCAN TO VISIT")
    phish = poster(PHISH, "PARKING FEE - SCAN TO PAY")
    cv2.imwrite(str(OUT / "qr_safe.png"), safe)
    cv2.imwrite(str(OUT / "qr_phish.png"), phish)
    h = max(safe.shape[0], phish.shape[0])
    pad = lambda im: cv2.copyMakeBorder(im, 0, h - im.shape[0], 0, 40, cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(str(OUT / "qr_multi.png"), np.hstack([pad(safe), pad(phish)]))
    print(f"{OUT} 에 qr_safe.png, qr_phish.png, qr_multi.png 생성")


if __name__ == "__main__":
    main()
