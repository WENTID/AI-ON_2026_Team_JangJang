"""웹 서버 (FastAPI). 화면(static/)을 제공하고 분석 API를 연다.

실행:  .venv\\Scripts\\python -m uvicorn app.main:app --reload
"""
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .analyzer import analyze
from .context import UPLOAD_DIR
from .qr_decode import decode_image
from .snapshot import collect
from .urls import InvalidUrl, normalize_input

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE_BYTES = 10 * 1024 * 1024

app = FastAPI(title="QR 피싱 분석기")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


class AnalyzeRequest(BaseModel):
    url: str
    image_id: str | None = None  # 카메라 프레임 / 업로드 원본 (맥락 분석용)


@app.post("/api/analyze")
def api_analyze(req: AnalyzeRequest):
    # 일반 def 로 두면 FastAPI가 별도 스레드에서 실행해, 분석 중에도 다른 요청을 받을 수 있다.
    try:
        url = normalize_input(req.url)
    except InvalidUrl as e:
        raise HTTPException(400, str(e))
    snap = collect(url)
    return analyze(snap, context_image_id=req.image_id, ai_url_cache=True)


@app.post("/api/image")
async def api_image(file: UploadFile = File(...), decode: bool = Form(True)):
    """이미지를 저장하고(맥락 분석용) QR 내용을 읽어 돌려준다."""
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "이미지가 너무 큽니다. (10MB 이하)")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "이미지 파일을 읽을 수 없습니다.")

    # 원본을 그대로 두지 않고 JPEG로 다시 저장한다. (이미지에 숨긴 다른 데이터 제거)
    image_id = uuid.uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(UPLOAD_DIR / f"{image_id}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])

    codes = []
    if decode:
        for text in decode_image(data):
            codes.append({"text": text, "is_url": text.strip().lower().startswith(("http://", "https://"))})
    return {"image_id": image_id, "codes": codes}


@app.get("/api/health")
def health():
    return {"ok": True, "ai": bool(config.env("GEMINI_API_KEY")), "safe_browsing": bool(config.env("SAFE_BROWSING_API_KEY"))}
