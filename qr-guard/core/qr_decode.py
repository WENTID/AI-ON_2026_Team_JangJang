"""QR 이미지 → URL.

opencv 의 QRCodeDetector 를 사용한다 (별도 시스템 라이브러리 불필요).
예외를 밖으로 던지지 않고, 실패하면 error 필드에 사유를 담아 반환한다.
"""

import cv2
import numpy as np


def _looks_like_url(text: str) -> bool:
    """http:// 또는 https:// 로 시작하면 URL 로 본다."""
    return text.lower().startswith(("http://", "https://"))


def _try_decode(img) -> str:
    """이미지 한 장에서 QR 해독을 시도한다. 실패하면 빈 문자열."""
    detector = cv2.QRCodeDetector()
    data, _points, _ = detector.detectAndDecode(img)
    return data or ""


def decode_qr(image_bytes: bytes) -> dict:
    """QR 이미지 바이트를 받아 URL 을 꺼낸다.

    반환: {"url": str | None, "raw": str | None, "error": str | None}
      - url : QR 내용이 URL 이면 그 값, 아니면 None
      - raw : QR 에 들어 있던 원문 (URL 이 아니어도 담음)
    """
    try:
        if not image_bytes:
            return {"url": None, "raw": None, "error": "이미지가 비어 있습니다."}

        # 바이트 → numpy 배열 → opencv 이미지
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return {"url": None, "raw": None, "error": "이미지 파일을 읽을 수 없습니다."}

        # 1차: 원본 그대로
        data = _try_decode(img)

        # 2차: 흑백 + 확대 (작거나 흐린 사진 대비)
        if not data:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            data = _try_decode(big)

        if not data:
            return {"url": None, "raw": None, "error": "이미지에서 QR 코드를 찾지 못했습니다."}

        data = data.strip()
        if _looks_like_url(data):
            return {"url": data, "raw": data, "error": None}
        return {"url": None, "raw": data, "error": "QR 내용이 URL 이 아닙니다."}

    except Exception as e:  # 어떤 오류도 밖으로 던지지 않는다
        return {"url": None, "raw": None, "error": f"QR 해독 중 오류: {e}"}
