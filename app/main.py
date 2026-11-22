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

from . import config, hierarchy, jev, notice
from .analyzer import analyze
from .qr_decode import decode_image, qr_area_ratio
from .uploads import UPLOAD_DIR, save_meta
from .snapshot import collect
from .urls import InvalidUrl, normalize_input

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_IMAGE_BYTES = 10 * 1024 * 1024

app = FastAPI(title="QR 피싱 분석기")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
def service_worker():
    # 서비스워커는 자기 파일이 있는 경로 아래만 관리할 수 있다.
    # /static/sw.js 가 아니라 루트(/sw.js)로 제공해야 첫 화면 "/" 까지 관리한다.
    # no-cache: 브라우저가 항상 서버에 새 버전이 있는지 확인하게 한다.
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


DEMO_SITE = Path(__file__).resolve().parent.parent / "demo_site"


@app.get("/demo-site/{name}")
def demo_site(name: str):
    """시연용 가짜 페이지 (demo_site/*.html). 폼은 어디로도 전송되지 않고, 검색엔진 수집을 막는다.
    URL만 보면 평범하지만 QR 안내문과 목적이 다른 페이지를 재현해 'QR 목적 비교'를 시연한다."""
    pages = {p.stem: p for p in DEMO_SITE.glob("*.html")}
    if name not in pages:
        raise HTTPException(404, "없는 시연 페이지")
    return FileResponse(pages[name], media_type="text/html", headers={"X-Robots-Tag": "noindex, nofollow"})


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
    # QR만 가까이 찍은 사진(QR 면적 비율이 큼)은 안내문이 없다고 보고 안내문 읽기(Gemini)를 하지 않는다.
    # 애매하면 Gemini가 읽어 본 결과(글자 수·readable)로 다시 거른다. (context.analyze_context)
    ratio = qr_area_ratio(img)
    save_meta(image_id, {"qr_ratio": ratio})
    if ratio is None or ratio < config.settings()["context"]["skip_qr_ratio"]:
        # 안내문 읽기를 지금 시작해 두면, 곧이어 오는 분석 요청에서 기다리는 시간이 줄어든다.
        notice.prefetch(image_id)

    codes = []
    if decode:
        for text in decode_image(data):
            codes.append({"text": text, "is_url": text.strip().lower().startswith(("http://", "https://"))})
    return {"image_id": image_id, "codes": codes}


@app.get("/api/rules")
def rules():
    """화면의 '예방 수칙' 목록. 문구는 config/rules_hierarchy.json 에서 읽는다."""
    ct = hierarchy.crime_type()
    return {"crime_type": ct["name"],
            "principles": [p["title"] for p in ct["principles"] if p["kind"] != "safe"]}


@app.get("/api/health")
def health():
    return {"ok": True, "ai": bool(config.env("GEMINI_API_KEY")), "safe_browsing": bool(config.env("SAFE_BROWSING_API_KEY")),
            "jev": jev.available()}
