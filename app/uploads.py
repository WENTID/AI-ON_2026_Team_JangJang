"""카메라 프레임·업로드 사진 저장 위치. (맥락 분석용, data/uploads/ 는 Git 제외)"""
import json

from . import config

UPLOAD_DIR = config.DATA_DIR / "uploads"


def image_path(image_id: str | None):
    """image_id 로 저장된 사진 경로. 영문·숫자만 허용해 다른 경로 접근을 막는다."""
    if not image_id or not image_id.isalnum():
        return None
    path = UPLOAD_DIR / f"{image_id}.jpg"
    return path if path.exists() else None


def save_meta(image_id: str, meta: dict) -> None:
    """사진 옆에 부가 정보(QR 면적 비율 등)를 저장한다. data/uploads/<id>.json"""
    if image_id.isalnum():
        (UPLOAD_DIR / f"{image_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_meta(image_id: str | None) -> dict:
    if not image_id or not image_id.isalnum():
        return {}
    path = UPLOAD_DIR / f"{image_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}
