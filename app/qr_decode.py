"""이미지에서 QR 코드를 모두 찾아 내용을 읽는다. (OpenCV 사용)"""
import cv2
import numpy as np


def decode_image(data: bytes) -> list[str]:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지 파일을 읽을 수 없습니다.")
    found: list[str] = []

    def add(texts):
        for t in texts:
            if t and t not in found:
                found.append(t)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    candidates = [img, gray]
    h, w = gray.shape
    if max(h, w) < 800:  # 작은 이미지는 키워서 다시 시도
        candidates.append(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    elif max(h, w) > 2000:  # 너무 큰 사진은 줄여서 다시 시도
        scale = 1600 / max(h, w)
        candidates.append(cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))

    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
    for det in detectors:
        for cand in candidates:
            try:
                ok, texts, _, _ = det.detectAndDecodeMulti(cand)
                if ok:
                    add(texts)
            except cv2.error:
                pass
            if not found:
                try:
                    text, _, _ = det.detectAndDecode(cand)
                    add([text])
                except cv2.error:
                    pass
    return found
