"""Jev 비교 테스트: Gemini와 Jev(TypeSafe)를 같은 문제로 비교한다. (2단계 맥락 일치 분석 준비)

A. 안내문 사진 이해: scripts/ocr_test.py 로 만든 77장을 Jev에 직접 넣어 안내 목적·결제 요구·스티커 의심을 묻는다.
   Gemini 쪽 답은 ocr_test.py run 결과(data/ocr_raw/)를 그대로 쓴다.
B. 맥락 일치: eval/context_cases.json 의 (안내문, 접속 페이지) 짝마다 '목적이 일치하는가'를 묻는다.
   - Gemini (글자)  : 안내문은 Gemini가 원본 사진에서 읽은 글자로 넣는다 (실제 서비스 흐름: OCR → 비교)
   - Jev (글자)     : 같은 글자를 Jev에 넣는다
   - Jev (사진)     : OCR 없이 안내문 사진을 Jev에 바로 넣는다
결과: eval/jev_results.md. 응답은 data/jev_raw/ 에 저장해 다시 돌리면 재사용(비용 없음).

사용법:  python scripts/ocr_test.py make && python scripts/ocr_test.py run   (먼저 한 번)
         python scripts/jev_test.py
필요:    .env 의 TYPESAFE_API_KEY, GEMINI_API_KEY
"""
import base64
import hashlib
import io
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx
from PIL import Image
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_judge, config  # noqa: E402
import ocr_test  # noqa: E402

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_PRICE_PER_MTOK = 0.042          # 입력 100만 토큰당 달러, 출력 무료 (docs.typesafe.ai/models, 2026-11 기준)
JEV_RETRY_DELAYS = [5, 15, 30, 60]  # 429(한도)·5xx·529(과부하) 때 기다렸다 다시 시도
IMG_MIN, IMG_MAX = 280, 1568        # Jev 이미지 가로·세로 허용 범위(px)
RAW_DIR = config.DATA_DIR / "jev_raw"
RESULT_MD = ROOT / "eval" / "jev_results.md"
CTX = json.loads((ROOT / "eval" / "context_cases.json").read_text(encoding="utf-8"))
PROMPT_VERSION = 1

MATCH_RULE = ("QR 안내문이 안내한 목적과, QR을 찍고 접속한 웹 페이지의 목적·요구 정보가 일치하는지 판단한다. "
              "안내문과 다른 서비스의 로그인, 안내 목적에 필요 없는 민감 정보(주민등록번호, 비밀번호, 카드 CVC, 계좌 비밀번호 등) 요구, "
              "앱 파일(apk) 직접 설치 요구는 불일치로 본다.")
MATCH_CRITERIA = {
    "일치": "페이지가 안내문이 말한 일을 하고, 그 일에 필요한 정보만 요구한다",
    "불일치": "페이지가 안내문과 다른 일을 하거나, 안내 목적에 필요 없는 민감 정보·앱 설치를 요구한다",
    "판단불가": "정보가 부족해 판단할 수 없다",
}


# ───────────────────────── Jev 호출 ─────────────────────────

def _jev_image(path: Path) -> tuple[str, dict]:
    """Jev 이미지 규격(가로·세로 280~1568px)에 맞춰 base64 로 바꾼다. 작은 사진은 키운다."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = max(1.0, IMG_MIN / min(w, h))
    scale = min(scale, IMG_MAX / max(w, h)) if max(w, h) * scale > IMG_MAX else scale
    if scale != 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    ref = "img-" + hashlib.sha1(data).hexdigest()[:16]
    return ref, {"type": "image", "format": "jpg", "data_base64": base64.b64encode(data).decode()}


def ask_jev(state, questions: dict, assets: dict | None = None) -> dict:
    """Jev 한 번 호출. 같은 요청이면 저장된 응답 재사용. 실패해도 예외 없이 status=error."""
    body = {"model": JEV_MODEL, "state": state, "questions": questions}
    if assets:
        body["assets"] = assets
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    raw = RAW_DIR / f"{key}.json"
    if raw.exists():
        return json.loads(raw.read_text(encoding="utf-8"))
    api_key = config.env("TYPESAFE_API_KEY")
    if not api_key:
        return {"status": "error", "error": "TYPESAFE_API_KEY 없음"}
    headers = {"Authorization": f"Bearer {api_key}"}
    if assets:
        headers["TypeSafe-Danger-Zone-Images"] = "true"
    for attempt in range(len(JEV_RETRY_DELAYS) + 1):
        start = time.perf_counter()
        try:
            r = httpx.post(JEV_URL, json=body, headers=headers, timeout=60)
        except httpx.HTTPError as e:
            if attempt < len(JEV_RETRY_DELAYS):
                time.sleep(JEV_RETRY_DELAYS[attempt])
                continue
            return {"status": "error", "error": f"{type(e).__name__}"}
        elapsed = time.perf_counter() - start
        if r.status_code == 200:
            d = r.json()
            out = {"status": "ok", "model": d.get("model"), "seconds": round(elapsed, 2), "answers": d["answers"],
                   "input_tokens": d.get("usage", {}).get("input_tokens", 0),
                   "output_tokens": d.get("usage", {}).get("output_tokens", 0)}
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            raw.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out
        if (r.status_code == 429 or r.status_code >= 500) and attempt < len(JEV_RETRY_DELAYS):
            print(f"  Jev 일시 오류 HTTP {r.status_code}, {JEV_RETRY_DELAYS[attempt]}초 뒤 재시도")
            time.sleep(JEV_RETRY_DELAYS[attempt])
            continue
        return {"status": "error", "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# ───────────────────────── Gemini 호출 (맥락 일치) ─────────────────────────

Match = Enum("Match", {k: k for k in MATCH_CRITERIA}, type=str)


class MatchResult(BaseModel):
    match: Match = Field(description="일치 / 불일치 / 판단불가")
    reason: str = Field(description="판단 근거 한 문장")


def ask_gemini_match(state: dict) -> dict:
    cfg = config.settings()["ai"]
    model = cfg["model"]
    key = hashlib.sha1(json.dumps([PROMPT_VERSION, "gemini-match", model, state], ensure_ascii=False,
                                  sort_keys=True).encode()).hexdigest()
    raw = RAW_DIR / f"gemini_{key}.json"
    if raw.exists():
        return json.loads(raw.read_text(encoding="utf-8"))
    from google.genai import types
    system = MATCH_RULE + "\n선택지:\n" + "\n".join(f"- {k}: {v}" for k, v in MATCH_CRITERIA.items())
    delays = cfg["eval_retry_delays_sec"]
    for attempt in range(len(delays) + 1):
        ai_judge._limiter.acquire(cfg["max_requests_per_minute"], wait=True)
        start = time.perf_counter()
        try:
            resp = ai_judge._get_client(long_timeout=True).models.generate_content(
                model=model, contents=json.dumps(state, ensure_ascii=False),
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                                   response_schema=MatchResult, temperature=0,
                                                   seed=cfg.get("seed", 0), candidate_count=1))
            elapsed = time.perf_counter() - start
            r = MatchResult.model_validate_json(resp.text).model_dump(mode="json")
            u = resp.usage_metadata
            out = {"status": "ok", "model": model, "seconds": round(elapsed, 2), "result": r,
                   "input_tokens": getattr(u, "prompt_token_count", 0) or 0}
            break
        except Exception as e:  # noqa: BLE001
            if attempt < len(delays) and ai_judge._is_temporary(e):
                time.sleep(delays[attempt])
                continue
            return {"status": "error", "error": f"{type(e).__name__}"}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ───────────────────────── A. 안내문 사진 이해 ─────────────────────────

def part_a() -> list[dict]:
    purposes = ocr_test.CASES["purposes"]
    gem_model = config.settings()["ai"]["model"]
    rows = []
    items = [it for it in ocr_test.collect_items()]
    for i, it in enumerate(items, 1):
        ref, asset = _jev_image(it["path"])
        questions = {
            "purpose": {"type": "choice", "instructions": "`photo` 안내문이 QR을 찍게 하는 목적은?",
                        "criteria": {p: f"QR을 찍게 하는 목적이 '{p}'" for p in purposes}},
            "payment": {"type": "noul",
                        "instructions": "`photo` 안내문이 QR을 찍은 뒤 돈을 내도록(결제·납부) 안내하는가?"},
            "sticker": {"type": "noul",
                        "instructions": "`photo`에서 QR코드가 안내문에 원래 인쇄된 것이 아니라 위에 덧붙인 스티커로 보이는가? "
                                        "(테두리, 그림자, 비뚤어짐, 종이 질감 차이)"},
        }
        jev = ask_jev({"photo": ref}, questions, {ref: asset})
        gem_raw = ocr_test.RAW_DIR / f"{gem_model}__v{ocr_test.PROMPT_VERSION}__{it['path'].parent.name}__{it['path'].stem}.json"
        gem = json.loads(gem_raw.read_text(encoding="utf-8")) if gem_raw.exists() else {"status": "missing"}
        row = {**it, "jev": jev, "gem": gem}
        if jev["status"] == "ok":
            a = jev["answers"]
            row["j_purpose"] = a["purpose"]["choice"]
            row["j_pay"] = a["payment"]["noul"] >= 0.5
            row["j_sticker"] = a["sticker"]["noul"] >= 0.5
            row["j_sticker_p"] = a["sticker"]["noul"]
        if gem.get("status") == "ok":
            g = gem["result"]
            row["g_purpose"], row["g_pay"], row["g_sticker"] = g["purpose"], g["requests_payment"], g["sticker_suspected"]
        status = "OK" if jev["status"] == "ok" else f"실패 {jev.get('error')}"
        print(f"[A {i}/{len(items)}] {it['path'].name}: Jev {status}"
              + (f", 목적 {row['j_purpose']}" if jev["status"] == "ok" else ""))
        rows.append(row)
    return rows


# ───────────────────────── B. 맥락 일치 ─────────────────────────

def _page_text(p: dict) -> dict:
    out = {"제목": p["title"], "표방 서비스": p["claimed_service"], "페이지 목적": p["page_purpose"],
           "입력 요구 정보": p["requested_info"] or ["없음"]}
    if p.get("download"):
        out["다운로드 파일"] = p["download"]
    return out


def pairs() -> list[dict]:
    out = []
    for c in CTX["cases"]:
        out.append({"case": c["id"], "kind": "일치", "label": "일치", "page": c["match"]})
        for m in c["mismatch"]:
            out.append({"case": c["id"], "kind": "불일치 (전혀 다른 페이지)", "label": "불일치",
                        "page": CTX["mismatch_pages"][m], "page_id": m})
        if c.get("tricky"):
            out.append({"case": c["id"], "kind": "불일치 (교묘: 목적은 비슷, 과도한 요구)", "label": "불일치",
                        "page": c["tricky"]})
    return out


def part_b() -> list[dict]:
    gem_model = config.settings()["ai"]["model"]
    rows = []
    ps = pairs()
    for i, p in enumerate(ps, 1):
        photo = ocr_test.SAMPLE_DIR / f"{p['case']}__original.jpg"
        gem_raw = ocr_test.RAW_DIR / f"{gem_model}__v{ocr_test.PROMPT_VERSION}__ocr_samples__{p['case']}__original.json"
        notice_text = json.loads(gem_raw.read_text(encoding="utf-8"))["result"]["full_text"]
        page = _page_text(p["page"])
        q = {"match": {"type": "choice", "instructions": MATCH_RULE, "criteria": MATCH_CRITERIA}}
        res = {
            "Gemini (글자)": ask_gemini_match({"QR 안내문 글자": notice_text, "접속한 페이지": page}),
            "Jev (글자)": ask_jev({"QR 안내문 글자": notice_text, "접속한 페이지": page}, q),
        }
        ref, asset = _jev_image(photo)
        res["Jev (사진)"] = ask_jev({"QR 안내문 사진": ref, "접속한 페이지": page}, q, {ref: asset})
        row = {**p, "res": res}
        for name, r in res.items():
            if r["status"] == "ok":
                row[name] = r["result"]["match"] if name.startswith("Gemini") else r["answers"]["match"]["choice"]
                if not name.startswith("Gemini"):
                    row[name + " 확률"] = r["answers"]["match"]["probabilities"].get("불일치")
        print(f"[B {i}/{len(ps)}] {p['case']} / {p['kind']}: 정답 {p['label']} → "
              + ", ".join(f"{n} {row.get(n, '실패')}" for n in res))
        rows.append(row)
    return rows


# ───────────────────────── 보고서 ─────────────────────────

def pct(n, d):
    return f"{n / d * 100:.0f}% ({n}/{d})" if d else "-"


def write_report(a: list[dict], b: list[dict]) -> None:
    gem_model = config.settings()["ai"]["model"]
    names = {**ocr_test.CONDITIONS}
    jev_calls = [r["jev"] for r in a] + [r["res"][n] for r in b for n in r["res"] if n.startswith("Jev")]
    jev_ok = [c for c in jev_calls if c["status"] == "ok"]
    jev_tok = sum(c.get("input_tokens", 0) for c in jev_ok)
    jev_model = next((c.get("model") for c in jev_ok if c.get("model")), JEV_MODEL)

    L = ["# Jev vs Gemini 비교 결과", "",
         f"- 실행: {datetime.now():%Y-%m-%d %H:%M} / Jev: {jev_model} / Gemini: {gem_model} (temperature 0)",
         f"- Jev 호출 {len(jev_calls)}건 중 성공 {len(jev_ok)}건, 입력 토큰 합계 {jev_tok:,}개 → "
         f"비용 약 ${jev_tok / 1e6 * JEV_PRICE_PER_MTOK:.4f} (입력 100만 토큰당 ${JEV_PRICE_PER_MTOK}, 출력 무료)",
         "- Jev는 글자를 받아 적지 않고 '선택지 + 확률'로 답하는 모델이라, 글자 인식률(CER)·기관명은 비교하지 않는다.",
         "- Jev 이미지 입력은 공식 문서상 아직 정식 서비스 수준이 아님(TypeSafe-Danger-Zone-Images). "
         "가로·세로 280px 미만 사진(멀리서 촬영)은 280px로 키워 보냈다.", ""]

    # A
    ja = [r for r in a if r["jev"]["status"] == "ok" and r["gem"].get("status") == "ok"]
    L += ["## A. 안내문 사진 이해 (사진 77장)", "",
          "| 항목 | Gemini | Jev |", "|---|---|---|",
          f"| 안내 목적 정확도 | {pct(sum(r['g_purpose'] == r['purpose'] for r in ja), len(ja))} "
          f"| {pct(sum(r['j_purpose'] == r['purpose'] for r in ja), len(ja))} |",
          f"| 결제 요구 판단 | {pct(sum(r['g_pay'] == r['payment'] for r in ja), len(ja))} "
          f"| {pct(sum(r['j_pay'] == r['payment'] for r in ja), len(ja))} |"]
    st = [r for r in ja if r["sticker"]]
    nst = [r for r in ja if not r["sticker"]]
    L += [f"| 스티커 탐지율 (붙인 사진에서 의심) | {pct(sum(r['g_sticker'] for r in st), len(st))} "
          f"| {pct(sum(r['j_sticker'] for r in st), len(st))} |",
          f"| 스티커 오탐률 (없는 사진에서 의심) | {pct(sum(r['g_sticker'] for r in nst), len(nst))} "
          f"| {pct(sum(r['j_sticker'] for r in nst), len(nst))} |",
          f"| 스티커 탐지율 / 오탐률 (Jev 확률 기준 0.8) | - "
          f"| {pct(sum(r['j_sticker_p'] >= 0.8 for r in st), len(st))} / "
          f"{pct(sum(r['j_sticker_p'] >= 0.8 for r in nst), len(nst))} |",
          f"| 평균 응답 시간 | {sum(r['gem']['seconds'] for r in ja) / max(1, len(ja)):.1f}초 "
          f"| {sum(r['jev']['seconds'] for r in ja) / max(1, len(ja)):.1f}초 |",
          f"| 사진 1장 입력 토큰 | {sum(r['gem'].get('input_tokens') or 0 for r in ja) / max(1, len(ja)):.0f} "
          f"| {sum(r['jev']['input_tokens'] for r in ja) / max(1, len(ja)):.0f} |", ""]
    L += ["### 촬영 조건별 안내 목적 정확도", "", "| 촬영 조건 | Gemini | Jev |", "|---|---|---|"]
    by = defaultdict(list)
    for r in ja:
        by[r["cond"]].append(r)
    for c, rs in by.items():
        L.append(f"| {names.get(c, c)} | {pct(sum(r['g_purpose'] == r['purpose'] for r in rs), len(rs))} "
                 f"| {pct(sum(r['j_purpose'] == r['purpose'] for r in rs), len(rs))} |")
    wrong = [r for r in ja if r["j_purpose"] != r["purpose"] or r["g_purpose"] != r["purpose"]]
    if wrong:
        L += ["", "| 틀린 사진 | 정답 목적 | Gemini | Jev |", "|---|---|---|---|"]
        for r in wrong:
            L.append(f"| {r['path'].name} | {r['purpose']} | {r['g_purpose']} | {r['j_purpose']} |")

    # B
    models = ["Gemini (글자)", "Jev (글자)", "Jev (사진)"]
    L += ["", f"## B. 맥락 일치 판정 (안내문 {len(CTX['cases'])}종 × 접속 페이지, {len(b)}쌍)", "",
          "안내문 목적과 접속 페이지 목적이 일치하는지 묻는다. '글자'는 Gemini가 원본 사진에서 읽은 글자를 넣은 것, "
          "'사진'은 OCR 없이 안내문 사진을 바로 넣은 것.", "",
          "| 항목 | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]

    def line(title, rows_):
        cells = []
        for m in models:
            ok = [r for r in rows_ if m in r]
            cells.append(pct(sum(r[m] == r["label"] for r in ok), len(ok)))
        L.append(f"| {title} | " + " | ".join(cells) + " |")

    line("**전체 정확도**", b)
    line("정상 페이지를 '일치'로 (오탐 없음)", [r for r in b if r["label"] == "일치"])
    line("전혀 다른 페이지를 '불일치'로", [r for r in b if r["kind"].startswith("불일치 (전혀")])
    line("교묘한 페이지를 '불일치'로", [r for r in b if r["kind"].startswith("불일치 (교묘")])
    secs = []
    for m in models:
        rs = [r["res"][m] for r in b if r["res"][m]["status"] == "ok"]
        secs.append(f"{sum(x['seconds'] for x in rs) / max(1, len(rs)):.1f}초")
    L.append("| 평균 응답 시간 | " + " | ".join(secs) + " |")
    toks = []
    for m in models:
        rs = [r["res"][m] for r in b if r["res"][m]["status"] == "ok"]
        toks.append(f"{sum(x.get('input_tokens', 0) for x in rs) / max(1, len(rs)):.0f}")
    L.append("| 1회 입력 토큰 | " + " | ".join(toks) + " |")

    L += ["", "### 틀린 판정", "", "| 안내문 | 페이지 종류 | 페이지 목적 | 정답 | " + " | ".join(models) + " |",
          "|---|---|---|---|" + "---|" * len(models)]
    bad = [r for r in b if any(r.get(m) != r["label"] for m in models)]
    for r in bad:
        cells = []
        for m in models:
            v = r.get(m, "실패")
            p = r.get(m + " 확률")
            cells.append(("**" + v + "**" if v != r["label"] else v) + (f" (불일치 {p:.2f})" if p is not None else ""))
        L.append(f"| {r['case']} | {r['kind']} | {r['page']['page_purpose']} | {r['label']} | " + " | ".join(cells) + " |")
    if not bad:
        L.append("| (없음) | | | | | | |")

    errs = [c for c in jev_calls if c["status"] != "ok"]
    if errs:
        L += ["", f"> Jev 실패 {len(errs)}건: " + "; ".join(sorted({e['error'][:80] for e in errs}))]
    L += ["", "> 참고: 안내문·페이지 모두 합성 데이터(가상 기관)다. 페이지는 실제 접속 대신 AI 판별 결과와 같은 형식의 요약으로 넣었다."]
    RESULT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


# ───────────────────────── 시연 페이지 ─────────────────────────

def write_viewer(a: list[dict], b: list[dict]) -> Path:
    """data/ocr_samples/jev_viewer.html: Gemini와 Jev 답을 사진·페이지 옆에 나란히 보여 준다."""
    import html as H
    esc = lambda s: H.escape(str(s))
    names = {**ocr_test.CONDITIONS}

    def bar(p: float, warn: bool) -> str:
        return (f'<span class="bar"><i style="width:{p * 100:.0f}%" class="{"r" if warn else "g"}"></i></span>'
                f' <small>{p:.2f}</small>')

    def ox(ok: bool) -> str:
        return '<b class="ok">O</b>' if ok else '<b class="ng">X</b>'

    # 요약
    ja = [r for r in a if r["jev"]["status"] == "ok" and r["gem"].get("status") == "ok"]
    st = [r for r in ja if r["sticker"]]
    nst = [r for r in ja if not r["sticker"]]
    thr = 0.8
    summary = f'''<table class="sum">
<tr><th></th><th>Gemini</th><th>Jev</th></tr>
<tr><td>안내 목적 정확도 (77장)</td><td>{pct(sum(r["g_purpose"] == r["purpose"] for r in ja), len(ja))}</td><td>{pct(sum(r["j_purpose"] == r["purpose"] for r in ja), len(ja))}</td></tr>
<tr><td>스티커 탐지율 / 오탐률 (기준 0.5)</td><td>{pct(sum(r["g_sticker"] for r in st), len(st))} / {pct(sum(r["g_sticker"] for r in nst), len(nst))}</td><td>{pct(sum(r["j_sticker"] for r in st), len(st))} / {pct(sum(r["j_sticker"] for r in nst), len(nst))}</td></tr>
<tr><td>스티커 탐지율 / 오탐률 (Jev 기준 {thr})</td><td>-</td><td><b>{pct(sum(r["j_sticker_p"] >= thr for r in st), len(st))} / {pct(sum(r["j_sticker_p"] >= thr for r in nst), len(nst))}</b></td></tr>
<tr><td>맥락 일치 정확도 ({len(b)}쌍)</td><td>{pct(sum(r.get("Gemini (글자)") == r["label"] for r in b), len(b))}</td><td>글자 {pct(sum(r.get("Jev (글자)") == r["label"] for r in b), len(b))} · 사진 {pct(sum(r.get("Jev (사진)") == r["label"] for r in b), len(b))}</td></tr>
<tr><td>평균 응답 시간</td><td>{sum(r["gem"]["seconds"] for r in ja) / len(ja):.1f}초</td><td>{sum(r["jev"]["seconds"] for r in ja) / len(ja):.1f}초</td></tr>
</table>'''

    # A 카드
    acards = []
    for r in ja:
        rel = r["path"].relative_to(ocr_test.SAMPLE_DIR).as_posix()
        ans = r["jev"]["answers"]
        pp = ans["purpose"].get("probabilities", {})
        acards.append(f'''<div class="card" data-cond="{r["cond"]}"><img src="{rel}" loading="lazy">
<div class="info"><h3>{esc(r["case"])} · {names[r["cond"]]}</h3>
<table><tr><th></th><th>정답</th><th>Gemini</th><th>Jev</th></tr>
<tr><td>안내 목적</td><td>{r["purpose"]}</td><td>{r["g_purpose"]} {ox(r["g_purpose"] == r["purpose"])}</td><td>{r["j_purpose"]} <small>({pp.get(r["j_purpose"], 0):.2f})</small> {ox(r["j_purpose"] == r["purpose"])}</td></tr>
<tr><td>결제 요구</td><td>{"예" if r["payment"] else "아니오"}</td><td>{"예" if r["g_pay"] else "아니오"} {ox(r["g_pay"] == r["payment"])}</td><td>{bar(ans["payment"]["noul"], False)} {ox(r["j_pay"] == r["payment"])}</td></tr>
<tr><td>스티커 의심</td><td>{"예" if r["sticker"] else "아니오"}</td><td>{"예" if r["g_sticker"] else "아니오"} {ox(r["g_sticker"] == r["sticker"])}</td><td>{bar(r["j_sticker_p"], r["j_sticker_p"] >= 0.5)} {ox((r["j_sticker_p"] >= thr) == r["sticker"])}<small> 기준 {thr}</small></td></tr>
</table><p class="t">응답 시간: Gemini {r["gem"]["seconds"]}초 · Jev {r["jev"]["seconds"]}초</p></div></div>''')

    # B 카드
    bcards = []
    for r in b:
        rel = f'{r["case"]}__original.jpg'
        page = _page_text(r["page"])
        prow = "".join(f"<tr><td>{esc(k)}</td><td>{esc(', '.join(v) if isinstance(v, list) else v)}</td></tr>"
                       for k, v in page.items())
        cells = []
        for m in ["Gemini (글자)", "Jev (글자)", "Jev (사진)"]:
            v = r.get(m, "실패")
            p = r.get(m + " 확률")
            extra = f'<br>{bar(p, p >= 0.5)}<br><small>불일치 확률</small>' if p is not None else ""
            reason = ""
            if m.startswith("Gemini") and r["res"][m]["status"] == "ok":
                reason = f'<br><small class="why">{esc(r["res"][m]["result"]["reason"])}</small>'
            cells.append(f'<td class="{"m" if v == "일치" else "x"}">{v} {ox(v == r["label"])}{extra}{reason}</td>')
        kind = "match" if r["label"] == "일치" else ("tricky" if "교묘" in r["kind"] else "diff")
        bcards.append(f'''<div class="card" data-cond="{kind}"><img src="{rel}" loading="lazy" class="sm">
<div class="info"><h3>{esc(r["case"])} 안내문 → <span class="k {kind}">{esc(r["kind"])}</span></h3>
<p class="label">QR을 찍고 접속한 페이지</p><table class="page">{prow}</table>
<p class="label">판정 (정답: <b>{r["label"]}</b>)</p>
<table class="res"><tr><th>Gemini (글자)</th><th>Jev (글자)</th><th>Jev (사진)</th></tr><tr>{"".join(cells)}</tr></table>
</div></div>''')

    abtn = "".join(f'<button data-f="{k}">{v}</button>' for k, v in names.items())
    page_html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jev vs Gemini 시연</title>
<style>
body{{font-family:"Malgun Gothic",sans-serif;margin:0;background:#f4f5f7;color:#222}}
header{{background:#183a6b;color:#fff;padding:16px 20px}} header h1{{margin:0;font-size:22px}} header p{{margin:6px 0 0;opacity:.85}}
.wrap{{padding:16px 20px}}
.sum{{background:#fff;border-radius:10px;box-shadow:0 1px 4px #0002;border-collapse:collapse;font-size:15px}}
.sum td,.sum th{{padding:8px 14px;border-bottom:1px solid #eee;text-align:left}}
.tabs{{display:flex;gap:8px;margin:18px 0 8px}} .tabs button{{font-size:16px;padding:8px 16px;border-radius:8px;border:2px solid #183a6b;background:#fff;color:#183a6b;cursor:pointer}}
.tabs button.on{{background:#183a6b;color:#fff}}
nav{{display:flex;gap:6px;flex-wrap:wrap;padding:8px 0;position:sticky;top:0;background:#f4f5f7;z-index:1}}
nav button{{border:1px solid #1e5aaa;background:#fff;color:#1e5aaa;border-radius:16px;padding:5px 11px;cursor:pointer}} nav button.on{{background:#1e5aaa;color:#fff}}
.grid{{display:grid;gap:14px}}
.card{{background:#fff;border-radius:10px;box-shadow:0 1px 4px #0002;display:flex;gap:16px;padding:14px}}
.card img{{width:230px;border-radius:6px;border:1px solid #ddd;align-self:flex-start;cursor:zoom-in}} .card img.sm{{width:150px}}
.card img.big{{width:520px}}
.info{{flex:1;min-width:0}} h3{{margin:0 0 8px;font-size:17px}}
table{{border-collapse:collapse;font-size:14px}} td,th{{border-bottom:1px solid #eee;padding:5px 10px;text-align:left;vertical-align:top}}
.ok{{color:#1a8a3a}} .ng{{color:#d22}} .t{{color:#888;font-size:13px;margin:6px 0 0}}
.bar{{display:inline-block;width:90px;height:9px;background:#eee;border-radius:5px;vertical-align:middle;overflow:hidden}}
.bar i{{display:block;height:100%}} .bar i.g{{background:#3a8ee6}} .bar i.r{{background:#e04848}}
.label{{margin:10px 0 4px;font-size:14px;font-weight:bold}} .page td:first-child{{color:#666;width:110px}}
.res td{{width:33%}} .res td.m{{background:#effaf2}} .res td.x{{background:#fff1f1}} .why{{color:#555}}
.k{{font-size:13px;padding:2px 8px;border-radius:10px}} .k.match{{background:#e2f6e8;color:#1a7a35}} .k.diff{{background:#fde4e4;color:#b02020}} .k.tricky{{background:#fff0d6;color:#a35d00}}
@media(max-width:700px){{.card{{flex-direction:column}} .card img,.card img.sm,.card img.big{{width:100%}}}}
</style></head><body>
<header><h1>Jev vs Gemini 비교 시연</h1><p>합성 안내문 11종 · 사진 77장 · 맥락 일치 {len(b)}쌍 · 사진을 누르면 크게 보입니다</p></header>
<div class="wrap">{summary}
<div class="tabs"><button data-tab="B" class="on">B. 맥락 일치 판정</button><button data-tab="A">A. 안내문 사진 이해</button></div>
<section id="B"><nav><button data-f="all" class="on">전체</button><button data-f="match">일치</button><button data-f="diff">전혀 다른 페이지</button><button data-f="tricky">교묘한 페이지</button></nav>
<div class="grid">{"".join(bcards)}</div></section>
<section id="A" hidden><nav><button data-f="all" class="on">전체</button>{abtn}</nav><div class="grid">{"".join(acards)}</div></section>
</div>
<script>
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{{
  document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("on",x===b));
  document.querySelectorAll("section").forEach(s=>s.hidden=s.id!==b.dataset.tab);}});
document.querySelectorAll("section").forEach(sec=>sec.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{{
  sec.querySelectorAll("nav button").forEach(x=>x.classList.toggle("on",x===b));
  sec.querySelectorAll(".card").forEach(c=>c.style.display=(b.dataset.f==="all"||c.dataset.cond===b.dataset.f)?"":"none");}}));
document.querySelectorAll(".card img").forEach(i=>i.onclick=()=>i.classList.toggle("big"));
</script></body></html>'''
    out = ocr_test.SAMPLE_DIR / "jev_viewer.html"
    out.write_text(page_html, encoding="utf-8")
    return out


def main() -> None:
    for k in ("TYPESAFE_API_KEY", "GEMINI_API_KEY"):
        if not config.env(k):
            sys.exit(f"{k} 가 .env 에 없습니다.")
    a = part_a()
    b = part_b()
    write_report(a, b)
    print(f"결과: {RESULT_MD}")
    print(f"시연 페이지: {write_viewer(a, b)}")


if __name__ == "__main__":
    main()
