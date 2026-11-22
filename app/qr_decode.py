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


def qr_area_ratio(img) -> float | None:
    """사진에서 QR코드가 차지하는 면적 비율(0~1). QR을 못 찾으면 None.

    맥락 분석 생략 판단용: QR만 가까이 찍은 사진은 비율이 크고, 안내문과 함께 찍은 사진은 작다.
    QR이 여러 개면 가장 큰 것 기준. 비율이라 사진 크기를 줄여 찾아도 값은 같다.
    """
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    if max(h, w) > 1600:  # 큰 사진은 줄여서 찾는다 (속도)
        scale = 1600 / max(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = gray.shape
    best = None
    for det in [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else []):
        try:
            ok, points = det.detectMulti(gray)
        except cv2.error:
            ok, points = False, None
        if not ok or points is None:
            try:
                ok, points = det.detect(gray)
                points = None if not ok or points is None else points.reshape(1, 4, 2)
            except cv2.error:
                points = None
        if points is None:
            continue
        for quad in points:
            area = abs(cv2.contourArea(quad.reshape(4, 2).astype(np.float32)))
            ratio = area / float(h * w)
            if ratio > 0 and (best is None or ratio > best):
                best = ratio
        if best is not None:
            break
    return None if best is None else round(min(best, 1.0), 4)
