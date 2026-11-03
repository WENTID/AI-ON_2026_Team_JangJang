"""QR 안내문 맥락 일치 분석 (요구사항 6-2). 2단계에서 완성한다.

1단계에서는 흐름만 준비한다.
  - 카메라 전체 프레임 / 업로드 원본 이미지를 저장하고 image_id 로 분석 요청에 포함
  - 결과 화면에 '안내문 목적 vs 실제 페이지 목적' 자리를 만든다
2단계 구현 계획: 이미지를 멀티모달 LLM에 보내 안내 맥락(예: "OO시 공영주차장 요금 결제")을 뽑고,
AI 사칭 판별의 page_purpose·실제 도메인과 비교해 불일치하면 ai_context_mismatch(강한 신호)를 추가한다.
"""
from . import config

UPLOAD_DIR = config.DATA_DIR / "uploads"


def image_path(image_id: str | None):
    if not image_id or not image_id.isalnum():
        return None
    path = UPLOAD_DIR / f"{image_id}.jpg"
    return path if path.exists() else None


def analyze_context(image_id: str | None, page_purpose: str | None) -> dict:
    path = image_path(image_id)
    if path is None:
        return {"status": "no_context", "message": "맥락 정보 없음 (URL 직접 입력 또는 안내문 이미지 없음)",
                "notice_purpose": None, "page_purpose": page_purpose, "signal": None}
    return {"status": "phase2", "message": "안내문 맥락 비교는 2단계에서 제공 예정",
            "image_id": image_id, "notice_purpose": None, "page_purpose": page_purpose, "signal": None}
