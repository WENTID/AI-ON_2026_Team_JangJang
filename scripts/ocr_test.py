"""OCR 테스트: QR 안내문 사진을 Gemini가 얼마나 잘 읽는지 측정한다. (2단계 맥락 일치 분석 준비)

1) make : eval/ocr_cases.json 의 합성 안내문을 이미지로 만들고, 촬영 장애 조건을 적용해 data/ocr_samples/ 에 저장
2) run  : 이미지마다 Gemini에 '글자 전부 + 안내 목적 + 기관명 + 결제 요구 + 스티커 의심'을 고정 JSON 형식으로 묻고 채점
          → eval/ocr_results.md (발표용 표). Gemini 응답은 data/ocr_raw/ 에 저장해 다시 돌리면 재사용

실제 안내문 사진도 테스트할 수 있다: data/ocr_samples/real/ 에 사진을 넣고, 같은 이름의 .json(정답) 을 두면 함께 채점한다.
  정답 .json 예: {"text": "안내문 글자 전부", "purpose": "주차요금 결제", "organization": "OO공단", "payment": true, "sticker": false}

사용법:  python scripts/ocr_test.py make
         python scripts/ocr_test.py run            (처음엔 분당 요청 한도 때문에 몇 분 걸림)
필요:    requirements-dev.txt (Pillow), .env 의 GEMINI_API_KEY
"""
import argparse
import json
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app import ai_judge  # noqa: E402  (Gemini 클라이언트·요청 한도·재시도 판단을 그대로 재사용)

CASES = json.loads((ROOT / "eval" / "ocr_cases.json").read_text(encoding="utf-8"))
SAMPLE_DIR = config.DATA_DIR / "ocr_samples"
RAW_DIR = config.DATA_DIR / "ocr_raw"
RESULT_MD = ROOT / "eval" / "ocr_results.md"
FONT = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
PROMPT_VERSION = 1

# 촬영 장애 조건 (이름, 설명)
CONDITIONS = {
    "original": "원본 (정면, 선명)",
    "blur": "흐림 (초점 안 맞음)",
    "tilt": "기울기 (비스듬히 촬영)",
    "glare": "빛 반사 (조명이 비침)",
    "dark": "어두움 + 잡음 (야간)",
    "far": "멀리서 촬영 (저해상도)",
    "sticker": "QR 위에 다른 QR 스티커 덧붙임",
}


# ───────────────────────── 1. 이미지 만들기 ─────────────────────────

def _qr_image(text: str, size: int) -> Image.Image:
    code = cv2.QRCodeEncoder.create().encode(text)
    code = cv2.copyMakeBorder(code, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=255)
    code = cv2.resize(code, (size, size), interpolation=cv2.INTER_NEAREST)
    return Image.fromarray(code).convert("RGB")


def _center_text(draw, y, text, font, fill, width):
    w = draw.textlength(text, font=font)
    draw.text(((width - w) / 2, y), text, font=font, fill=fill)


def make_poster(case: dict) -> Image.Image:
    """안내문 한 장: 색 띠 제목 + 본문 + QR + 하단 안내."""
    W, H = 800, 1100
    color = tuple(case["color"])
    img = Image.new("RGB", (W, H), (250, 250, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 150], fill=color)
    _center_text(d, 45, case["title"], ImageFont.truetype(FONT_BOLD, 50), "white", W)
    body = ImageFont.truetype(FONT, 34)
    y = 200
    for i, line in enumerate(case["lines"]):
        font = ImageFont.truetype(FONT_BOLD, 38) if i == 0 else body
        _center_text(d, y, line, font, (30, 30, 30), W)
        y += 62
    img.paste(_qr_image(case["qr"], 360), ((W - 360) // 2, y + 30))
    d.rectangle([0, H - 70, W, H], fill=color)
    return img


def _to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def apply_condition(img: Image.Image, cond: str, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)  # 다시 만들어도 같은 이미지
    a = _to_cv(img)
    h, w = a.shape[:2]
    if cond == "blur":
        a = cv2.GaussianBlur(a, (0, 0), sigmaX=3.0)
    elif cond == "tilt":
        # 사진을 비스듬히 찍은 것처럼 원근 왜곡 + 회전, 바깥은 회색 벽
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32([[w * 0.12, h * 0.06], [w * 0.95, 0], [w * 0.88, h], [w * 0.02, h * 0.9]])
        m = cv2.getPerspectiveTransform(src, dst)
        a = cv2.warpPerspective(a, m, (w, h), borderValue=(150, 150, 145))
        r = cv2.getRotationMatrix2D((w / 2, h / 2), 8, 1.0)
        a = cv2.warpAffine(a, r, (w, h), borderValue=(150, 150, 145))
    elif cond == "glare":
        # 오른쪽 위에서 비치는 조명: 흰색 타원 그라데이션
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w * 0.62, h * 0.32
        dist = np.sqrt(((xx - cx) / (w * 0.32)) ** 2 + ((yy - cy) / (h * 0.16)) ** 2)
        mask = np.clip(1.15 - dist, 0, 1)[..., None] ** 1.5
        a = (a * (1 - mask) + 255 * mask).astype(np.uint8)
    elif cond == "dark":
        a = (255 * (a / 255.0) ** 2.2 * 0.55).astype(np.float32)
        a += rng.normal(0, 12, a.shape)
        a = np.clip(a, 0, 255).astype(np.uint8)
    elif cond == "far":
        a = cv2.resize(a, (int(w * 0.3), int(h * 0.3)), interpolation=cv2.INTER_AREA)
    elif cond == "sticker":
        # 원래 QR 위에 살짝 비뚤게 붙인 다른 QR 스티커(그림자 포함). 글자는 그대로 둔다.
        out = img.copy()
        sticker = Image.new("RGB", (330, 330), (255, 255, 255))
        sticker.paste(_qr_image("https://pay-check.malicious.example/x", 300), (15, 15))
        sticker = sticker.rotate(-4, expand=True, fillcolor=(0, 0, 0))
        mask = Image.new("L", (330, 330), 255).rotate(-4, expand=True, fillcolor=0)
        qr_y = 200 + 62 * len(_case_by_seed(seed)["lines"]) + 30
        x, y = (w - 360) // 2 + 30, qr_y + 10
        shadow = Image.new("RGB", sticker.size, (90, 90, 90))
        out.paste(shadow, (x + 8, y + 8), mask)
        out.paste(sticker, (x, y), mask)
        return out
    return _to_pil(a)


def _case_by_seed(seed: int) -> dict:
    return CASES["cases"][seed // 100]


def cmd_make() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for ci, case in enumerate(CASES["cases"]):
        poster = make_poster(case)
        for k, cond in enumerate(CONDITIONS):
            out = apply_condition(poster, cond, seed=ci * 100 + k)
            out.save(SAMPLE_DIR / f"{case['id']}__{cond}.jpg", quality=88)
            n += 1
    (SAMPLE_DIR / "real").mkdir(exist_ok=True)
    print(f"{SAMPLE_DIR} 에 {n}장 생성 (안내문 {len(CASES['cases'])}종 × 조건 {len(CONDITIONS)}가지)")


# ───────────────────────── 2. Gemini로 읽기 ─────────────────────────

Purpose = Enum("Purpose", {p: p for p in CASES["purposes"]}, type=str)

OCR_PROMPT = f"""너는 QR코드가 붙은 안내문 사진을 읽는 분석가다. 사진에 보이는 사실만 답한다.
- full_text: 사진 속 글자를 위에서 아래로 빠짐없이, 보이는 그대로 적는다. 줄은 줄바꿈으로 구분. 추측해서 고치지 않는다. 읽을 수 없는 글자는 생략.
- purpose: 이 안내문이 QR을 찍게 하는 목적. 다음 중 하나: {", ".join(CASES["purposes"])}
- organization: 안내문을 붙인 기관·가게 이름 (보이는 그대로). 없으면 null.
- requests_payment: QR을 찍은 뒤 돈을 내도록(결제·납부) 안내하면 true.
- sticker_suspected: QR코드가 원래 인쇄된 것이 아니라 위에 덧붙인 스티커로 보이면 true (테두리·그림자·비뚤어짐·종이 질감 차이).
- readable: 글자를 대부분 읽을 수 있으면 true.
"""


class OcrResult(BaseModel):
    full_text: str = Field(description="사진 속 글자 전부")
    purpose: Purpose = Field(description="QR 안내 목적")
    organization: str | None = Field(description="기관·가게 이름")
    requests_payment: bool = Field(description="결제·납부 요구 여부")
    sticker_suspected: bool = Field(description="QR 위에 스티커를 덧붙인 흔적")
    readable: bool = Field(description="글자를 대부분 읽을 수 있는지")


def ask_gemini(image_path: Path) -> dict:
    """이미지 한 장을 Gemini에 묻는다. 저장된 응답이 있으면 재사용. 실패해도 예외 없이 status=error."""
    cfg = config.settings()["ai"]
    model = cfg["model"]
    raw = RAW_DIR / f"{model}__v{PROMPT_VERSION}__{image_path.parent.name}__{image_path.stem}.json"
    if raw.exists():
        return json.loads(raw.read_text(encoding="utf-8"))

    from google.genai import types
    data = image_path.read_bytes()
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    delays = cfg["eval_retry_delays_sec"]
    for attempt in range(len(delays) + 1):
        ai_judge._limiter.acquire(cfg["max_requests_per_minute"], wait=True)
        start = time.perf_counter()
        try:
            resp = ai_judge._get_client(long_timeout=True).models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type=mime), "이 안내문 사진을 분석해 줘."],
                config=types.GenerateContentConfig(
                    system_instruction=OCR_PROMPT,
                    response_mime_type="application/json",
                    response_schema=OcrResult,
                    temperature=0,
                    seed=cfg.get("seed", 0),
                    candidate_count=1,
                ),
            )
            elapsed = time.perf_counter() - start
            result = OcrResult.model_validate_json(resp.text).model_dump(mode="json")
            usage = resp.usage_metadata
            out = {"status": "ok", "model": model, "seconds": round(elapsed, 2), "result": result,
                   "input_tokens": getattr(usage, "prompt_token_count", None),
                   "output_tokens": getattr(usage, "candidates_token_count", None)}
            break
        except Exception as e:  # noqa: BLE001
            if attempt < len(delays) and ai_judge._is_temporary(e):
                print(f"  일시 오류({type(e).__name__}), {delays[attempt]}초 뒤 재시도")
                time.sleep(delays[attempt])
                continue
            return {"status": "error", "model": model, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ───────────────────────── 3. 채점 ─────────────────────────

def _norm(s: str) -> str:
    """띄어쓰기·줄바꿈·문장부호 차이는 오류로 세지 않는다. 글자(한글·영문·숫자)만 비교."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(pred: str, truth: str) -> float:
    """글자 오류율(CER) = 틀린 글자 수 / 정답 글자 수. 0이 완벽, 1 이상은 1로 자름."""
    p, t = _norm(pred), _norm(truth)
    return min(1.0, _edit_distance(p, t) / max(1, len(t)))


def _org_ok(pred: str | None, truth: str) -> bool:
    p, t = _norm(pred or ""), _norm(truth)
    return bool(p) and (p in t or t in p)


def truth_text(case: dict) -> str:
    return "\n".join([case["title"], *case["lines"]])


def collect_items() -> list[dict]:
    """채점할 이미지 목록: 합성 안내문 + data/ocr_samples/real/ 의 실제 사진(정답 json 있는 것만)."""
    items = []
    for case in CASES["cases"]:
        for cond in CONDITIONS:
            path = SAMPLE_DIR / f"{case['id']}__{cond}.jpg"
            if path.exists():
                items.append({"path": path, "cond": cond, "case": case["id"], "text": truth_text(case),
                              "purpose": case["purpose"], "organization": case["organization"],
                              "payment": case["payment"], "sticker": cond == "sticker"})
    real = SAMPLE_DIR / "real"
    if real.exists():
        for path in sorted(real.iterdir()):
            ans = path.with_suffix(".json")
            if path.suffix.lower() in (".jpg", ".jpeg", ".png") and ans.exists():
                t = json.loads(ans.read_text(encoding="utf-8"))
                items.append({"path": path, "cond": "real", "case": path.stem, "text": t["text"],
                              "purpose": t["purpose"], "organization": t["organization"],
                              "payment": t["payment"], "sticker": t.get("sticker", False)})
    return items


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.0f}% ({n}/{d})" if d else "-"


def cmd_run() -> None:
    if not config.env("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY 가 .env 에 없습니다.")
    items = collect_items()
    if not items:
        sys.exit("이미지가 없습니다. 먼저 python scripts/ocr_test.py make")
    rows = []
    for i, it in enumerate(items, 1):
        res = ask_gemini(it["path"])
        row = {**it, "status": res["status"]}
        if res["status"] == "ok":
            r = res["result"]
            row.update(cer=cer(r["full_text"], it["text"]), purpose_ok=r["purpose"] == it["purpose"],
                       org_ok=_org_ok(r["organization"], it["organization"]),
                       pay_ok=r["requests_payment"] == it["payment"], sticker_pred=r["sticker_suspected"],
                       readable=r["readable"], seconds=res["seconds"], in_tok=res.get("input_tokens") or 0,
                       out_tok=res.get("output_tokens") or 0, pred_purpose=r["purpose"],
                       pred_org=r["organization"], pred_text=r["full_text"])
            print(f"[{i}/{len(items)}] {it['path'].name}: CER {row['cer']:.3f}, 목적 {'O' if row['purpose_ok'] else 'X'}"
                  f", {res['seconds']}초")
        else:
            row["error"] = res["error"]
            print(f"[{i}/{len(items)}] {it['path'].name}: 실패 - {res['error']}")
        rows.append(row)
    write_report(rows)
    print(f"결과: {RESULT_MD}")


def write_report(rows: list[dict]) -> None:
    model = config.settings()["ai"]["model"]
    ok = [r for r in rows if r["status"] == "ok"]
    by = defaultdict(list)
    for r in ok:
        by[r["cond"]].append(r)
    conds = [c for c in [*CONDITIONS, "real"] if c in by]
    names = {**CONDITIONS, "real": "실제 촬영 사진"}

    L = ["# OCR 테스트 결과 (Gemini)", "",
         f"- 실행: {datetime.now():%Y-%m-%d %H:%M} / 모델: {model} / temperature 0, 고정 질문·고정 JSON 형식",
         f"- 데이터: 합성 안내문 {len(CASES['cases'])}종 × 촬영 조건 {len(CONDITIONS)}가지"
         + (f" + 실제 사진 {len(by['real'])}장" if by.get("real") else ""),
         f"- 성공 {len(ok)} / 전체 {len(rows)}"
         + (f" (실패 {len(rows) - len(ok)}건: AI 오류·한도 초과)" if len(ok) < len(rows) else ""),
         "- 글자 오류율(CER): 틀린 글자 수 ÷ 정답 글자 수. 0%가 완벽. 띄어쓰기·문장부호 차이는 제외하고 글자만 비교",
         "- 한국어 인식률 = 100% − CER", "",
         "## 1. 촬영 조건별 성능", "",
         "| 촬영 조건 | 한국어 인식률 (평균) | 완벽히 읽음 (CER 0) | 안내 목적 정확도 | 기관명 정확도 | 결제 요구 판단 | 스티커 의심 판정 | 평균 응답 시간 |",
         "|---|---|---|---|---|---|---|---|"]
    for c in conds:
        rs = by[c]
        n = len(rs)
        acc = 1 - sum(r["cer"] for r in rs) / n
        sticker = sum(r["sticker_pred"] for r in rs)
        L.append(f"| {names[c]} | {acc * 100:.1f}% | {pct(sum(r['cer'] == 0 for r in rs), n)} "
                 f"| {pct(sum(r['purpose_ok'] for r in rs), n)} | {pct(sum(r['org_ok'] for r in rs), n)} "
                 f"| {pct(sum(r['pay_ok'] for r in rs), n)} | {sticker}/{n}건 의심 "
                 f"| {sum(r['seconds'] for r in rs) / n:.1f}초 |")
    if ok:
        n = len(ok)
        L.append(f"| **전체** | **{(1 - sum(r['cer'] for r in ok) / n) * 100:.1f}%** "
                 f"| {pct(sum(r['cer'] == 0 for r in ok), n)} | **{pct(sum(r['purpose_ok'] for r in ok), n)}** "
                 f"| {pct(sum(r['org_ok'] for r in ok), n)} | {pct(sum(r['pay_ok'] for r in ok), n)} | - "
                 f"| {sum(r['seconds'] for r in ok) / n:.1f}초 |")

    # 스티커 탐지
    st = [r for r in ok if r["sticker"]]
    nst = [r for r in ok if not r["sticker"]]
    L += ["", "## 2. 덧붙인 QR 스티커 탐지", "",
          "| 항목 | 결과 |", "|---|---|",
          f"| 스티커를 붙인 사진에서 '스티커 의심'으로 답함 (탐지율) | {pct(sum(r['sticker_pred'] for r in st), len(st))} |",
          f"| 스티커가 없는 사진에서 '스티커 의심'으로 답함 (오탐률) | {pct(sum(r['sticker_pred'] for r in nst), len(nst))} |"]

    # 비용
    if ok:
        L += ["", "## 3. 사용량", "",
              f"- 사진 1장당 평균 입력 토큰 {sum(r['in_tok'] for r in ok) / len(ok):.0f}개, "
              f"출력 토큰 {sum(r['out_tok'] for r in ok) / len(ok):.0f}개",
              f"- 평균 응답 시간 {sum(r['seconds'] for r in ok) / len(ok):.1f}초 "
              f"(가장 느림 {max(r['seconds'] for r in ok):.1f}초)",
              "- 무료 등급 API 키로 실행 (요청 한도: settings.json 의 ai.max_requests_per_minute)"]

    # 틀린 사례
    L += ["", "## 4. 틀린 사례", "", "| 사진 | 촬영 조건 | CER | 정답 목적 → AI 답 | AI가 읽은 기관명 |", "|---|---|---|---|---|"]
    bad = sorted([r for r in ok if r["cer"] > 0.05 or not r["purpose_ok"] or not r["org_ok"]],
                 key=lambda r: -r["cer"])
    for r in bad[:20]:
        L.append(f"| {r['path'].name} | {names[r['cond']]} | {r['cer'] * 100:.0f}% "
                 f"| {r['purpose']} → {r['pred_purpose']} | {r['pred_org']} |")
    if not bad:
        L.append("| (없음) | | | | |")
    for r in rows:
        if r["status"] != "ok":
            L.append(f"| {r['path'].name} | {names.get(r['cond'], r['cond'])} | 실패 | {r['error'][:60]} | |")

    L += ["", "> 참고: 합성 안내문은 깨끗한 인쇄 글꼴(맑은 고딕)이라 실제 사진보다 쉬울 수 있다. "
          "촬영 장애는 코드로 흉내 낸 것이다. 실제 사진은 data/ocr_samples/real/ 에 정답과 함께 넣으면 같은 표에 추가된다."]
    RESULT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


# ───────────────────────── 4. 시연 페이지 ─────────────────────────

def _diff_html(pred: str, truth: str) -> str:
    """AI가 읽은 글자 중 정답과 다른 부분을 빨간색, 빠뜨린 부분을 회색 취소선으로 표시."""
    import difflib
    import html
    out = []
    sm = difflib.SequenceMatcher(None, truth, pred, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(html.escape(pred[j1:j2]))
        else:
            if i2 > i1 and _norm(truth[i1:i2]):
                out.append(f'<del>{html.escape(truth[i1:i2])}</del>')
            if j2 > j1:
                cls = "bad" if _norm(pred[j1:j2]) else ""
                out.append(f'<ins class="{cls}">{html.escape(pred[j1:j2])}</ins>')
    return "".join(out)


def cmd_view() -> None:
    """data/ocr_samples/viewer.html: 사진 옆에 정답과 AI 답을 나란히 보여 주는 시연 페이지 (지금까지 받은 응답만)."""
    import html
    model = config.settings()["ai"]["model"]
    cards, done = [], 0
    names = {**CONDITIONS, "real": "실제 촬영 사진"}
    items = collect_items()
    for it in items:
        raw = RAW_DIR / f"{model}__v{PROMPT_VERSION}__{it['path'].parent.name}__{it['path'].stem}.json"
        rel = it["path"].relative_to(SAMPLE_DIR).as_posix()
        if not raw.exists():
            cards.append(f'<div class="card wait" data-cond="{it["cond"]}"><img src="{rel}" loading="lazy">'
                         f'<div class="info"><h3>{it["case"]} · {names[it["cond"]]}</h3><p>분석 대기 중…</p></div></div>')
            continue
        res = json.loads(raw.read_text(encoding="utf-8"))
        if res["status"] != "ok":
            continue
        done += 1
        r = res["result"]
        c = cer(r["full_text"], it["text"])
        mark = lambda ok: '<b class="ok">O</b>' if ok else '<b class="ng">X</b>'
        st_ok = r["sticker_suspected"] == it["sticker"]
        cards.append(f'''<div class="card" data-cond="{it["cond"]}">
<img src="{rel}" loading="lazy">
<div class="info">
<h3>{html.escape(it["case"])} · {names[it["cond"]]} <span class="acc">인식률 {(1 - c) * 100:.0f}%</span> <span class="sec">{res["seconds"]}초</span></h3>
<table>
<tr><th></th><th>정답</th><th>Gemini</th><th></th></tr>
<tr><td>안내 목적</td><td>{it["purpose"]}</td><td>{r["purpose"]}</td><td>{mark(r["purpose"] == it["purpose"])}</td></tr>
<tr><td>기관명</td><td>{html.escape(it["organization"])}</td><td>{html.escape(r["organization"] or "-")}</td><td>{mark(_org_ok(r["organization"], it["organization"]))}</td></tr>
<tr><td>결제 요구</td><td>{"예" if it["payment"] else "아니오"}</td><td>{"예" if r["requests_payment"] else "아니오"}</td><td>{mark(r["requests_payment"] == it["payment"])}</td></tr>
<tr><td>스티커 의심</td><td>{"예" if it["sticker"] else "아니오"}</td><td>{"예" if r["sticker_suspected"] else "아니오"}</td><td>{mark(st_ok)}</td></tr>
</table>
<p class="label">Gemini가 읽은 글자 <small>(빨강: 잘못 읽음, 회색 취소선: 빠뜨림)</small></p>
<pre>{_diff_html(r["full_text"], it["text"])}</pre>
</div></div>''')
    buttons = "".join(f'<button data-f="{k}">{v}</button>' for k, v in names.items()
                      if any(i["cond"] == k for i in items))
    page = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OCR 시연</title>
{'<meta http-equiv="refresh" content="10">' if done < len(items) else ''}
<style>
body{{font-family:"Malgun Gothic",sans-serif;margin:0;background:#f4f5f7;color:#222}}
header{{background:#1e5aaa;color:#fff;padding:16px 20px}} header h1{{margin:0;font-size:22px}}
header p{{margin:6px 0 0;opacity:.9}}
nav{{padding:12px 20px;display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;background:#f4f5f7;z-index:1}}
nav button{{border:1px solid #1e5aaa;background:#fff;color:#1e5aaa;border-radius:16px;padding:6px 12px;cursor:pointer}}
nav button.on{{background:#1e5aaa;color:#fff}}
main{{padding:0 20px 40px;display:grid;gap:16px}}
.card{{background:#fff;border-radius:10px;box-shadow:0 1px 4px #0002;display:flex;gap:16px;padding:14px}}
.card img{{width:260px;height:auto;border-radius:6px;border:1px solid #ddd;align-self:flex-start;cursor:zoom-in}}
.card img.big{{width:560px}}
.info{{flex:1;min-width:0}} h3{{margin:0 0 8px;font-size:17px}}
.acc{{background:#e7f1ff;color:#1e5aaa;border-radius:10px;padding:2px 8px;font-size:13px}}
.sec{{color:#888;font-size:13px}}
table{{border-collapse:collapse;font-size:14px;margin-bottom:8px}} td,th{{border-bottom:1px solid #eee;padding:4px 10px;text-align:left}}
.ok{{color:#1a8a3a}} .ng{{color:#d22}}
.label{{margin:6px 0 2px;font-size:14px}} pre{{white-space:pre-wrap;background:#fafafa;border:1px solid #eee;padding:8px;font-size:14px;margin:0;font-family:inherit}}
ins{{text-decoration:none}} ins.bad{{background:#ffd6d6;color:#b00}} del{{color:#999}}
.wait{{opacity:.5}}
@media(max-width:700px){{.card{{flex-direction:column}} .card img,.card img.big{{width:100%}}}}
</style></head><body>
<header><h1>QR 안내문 OCR 시연 (Gemini)</h1>
<p>모델 {model} · 분석 완료 {done}/{len(items)}장 · 사진을 누르면 크게 보입니다 · 만든 시각 {datetime.now():%H:%M:%S}
{' · <b>분석 중이라 10초마다 자동 새로고침</b>' if done < len(items) else ' · 분석 완료'}</p></header>
<nav><button data-f="all" class="on">전체</button>{buttons}</nav>
<main>{"".join(cards)}</main>
<script>
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{{
  document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("on",x===b));
  document.querySelectorAll(".card").forEach(c=>c.style.display=(b.dataset.f==="all"||c.dataset.cond===b.dataset.f)?"":"none");}});
document.querySelectorAll(".card img").forEach(i=>i.onclick=()=>i.classList.toggle("big"));
</script></body></html>'''
    out = SAMPLE_DIR / "viewer.html"
    out.write_text(page, encoding="utf-8")
    print(f"시연 페이지: {out} (분석 완료 {done}/{len(items)}장)")


def main() -> None:
    p = argparse.ArgumentParser(description="Gemini OCR 테스트")
    p.add_argument("cmd", choices=["make", "run", "view"])
    args = p.parse_args()
    {"make": cmd_make, "run": cmd_run, "view": cmd_view}[args.cmd]()


if __name__ == "__main__":
    main()
