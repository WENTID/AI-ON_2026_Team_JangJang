"""시연 사례 견고성 테스트: 실서비스와 같은 경로(app.notice → app.ai_judge → app.context)로
시연 포스터 5장 × 촬영 장애 조건 + 직접 찍은 사진을 돌려 OCR·목적 일치 판정이 흔들리지 않는지 본다.
또 안내문 글자에 OCR식 오타(5~30%)를 넣어 Jev 판정이 유지되는지(오타 내성) 본다.

사용법:  python scripts/demo_eval.py [--user 업로드사진.jpg ...]
결과:    eval/demo_eval_results.md
"""
import argparse
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_judge, config, context, jev, notice  # noqa: E402
from app.page import extract  # noqa: E402
from app.uploads import UPLOAD_DIR  # noqa: E402
import demo_cases  # noqa: E402
import jev_context_lab as lab  # noqa: E402
import ocr_test  # noqa: E402

CONDS = {k: v for k, v in ocr_test.CONDITIONS.items() if k != "sticker"}  # 사례 5가 스티커 사례라 조건에서 제외
EXPECT = {1: "불일치", 2: "불일치", 3: "불일치", 4: "일치", 5: "불일치"}


def save_upload(img: Image.Image) -> str:
    image_id = uuid.uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(UPLOAD_DIR / f"{image_id}.jpg", quality=85)
    return image_id


def page_info(page_name: str, base: str):
    html = (ROOT / "demo_site" / f"{page_name}.html").read_text(encoding="utf-8")
    url = f"{base}/demo-site/{page_name}"
    page = extract(html, url)
    res = ai_judge.judge_page(page, url.split("/")[2], wait=True)
    return page, (res["result"] if res["status"] == "ok" else None)


def pct(n, d):
    return f"{n / d * 100:.0f}% ({n}/{d})" if d else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", nargs="*", default=[], help="직접 찍은 사진과 사례 번호. 예: data/uploads/abc.jpg:5")
    a = ap.parse_args()
    base = demo_cases.DEFAULT_BASE
    saved = demo_cases.OUT / "demo_cases.json"
    if saved.exists():
        import json
        base = json.loads(saved.read_text(encoding="utf-8"))["base"]

    pages = {}
    for _, _, pg, _, _, _ in demo_cases.CASES:
        if pg not in pages:
            pages[pg] = page_info(pg, base)
            print(f"페이지 판별 {pg}: {(pages[pg][1] or {}).get('page_purpose')}")

    # ---------- 1·2. OCR + 목적 일치 (촬영 조건별) ----------
    rows = []
    jobs = []
    for n, notice_id, pg, sticker, desc, _ in demo_cases.CASES:
        poster = Image.open(demo_cases.poster_path(n, notice_id, pg, sticker)).convert("RGB")
        for k, cond in enumerate(CONDS):
            img = ocr_test.apply_condition(poster, cond, seed=n * 100 + k) if cond != "original" else poster
            jobs.append((n, notice_id, pg, sticker, cond, img))
    for spec in a.user:  # "사진경로:사례번호"
        f, n = spec.rsplit(":", 1)
        c = next(c for c in demo_cases.CASES if c[0] == int(n))
        jobs.append((c[0], c[1], c[2], c[3], "user", Image.open(f).convert("RGB")))
    for n, notice_id, pg, sticker, cond, img in jobs:
        image_id = save_upload(img)
        notice.prefetch(image_id)
        page, ai_res = pages[pg]
        ctx = context.analyze_context(image_id, page=page, ai_result=ai_res)
        case = next(c for c in ocr_test.CASES["cases"] if c["id"] == notice_id)
        nt = ctx["notice"]
        row = {"n": n, "cond": cond, "notice": notice_id, "page": pg, "sticker": sticker, "status": ctx["status"],
               "decision": ctx["decision"], "p": ctx["p_mismatch"], "msg": ctx["message"]}
        if nt:
            row.update(cer=ocr_test.cer(nt["text"], ocr_test.truth_text(case)), purpose_ok=nt["purpose"] == case["purpose"],
                       sticker_pred=nt["sticker"], text=nt["text"])
        rows.append(row)
        print(f"사례 {n} {cond:8s}: OCR CER {row.get('cer', 1):.3f} 목적 {'O' if row.get('purpose_ok') else 'X'} "
              f"스티커 {row.get('sticker_pred')} → {ctx['decision']} ({ctx['p_mismatch']}) {ctx['status']}")

    # ---------- 3. 오타 내성 (실서비스 질문 문구 + 실제 페이지 판별 결과) ----------
    typo_rows = []
    for n, notice_id, pg, sticker, desc, _ in demo_cases.CASES:
        case = next(c for c in ocr_test.CASES["cases"] if c["id"] == notice_id)
        clean = ocr_test.truth_text(case)
        page, ai_res = pages[pg]
        summary = context.page_summary(page, ai_res, None)
        for rate in [0.0] + lab.RATES:
            for s in ([0] if rate == 0 else lab.SEEDS):
                text = clean if rate == 0 else lab.add_typos(clean, rate, seed=n * 1000 + s)
                r = jev.ask({"QR 안내문": text, "접속한 페이지": summary}, {"match": context.QUESTION})
                if r["status"] == "ok":
                    a_ = r["answers"]["match"]
                    typo_rows.append({"n": n, "rate": rate, "seed": s, "cer": ocr_test.cer(text, clean),
                                      "decision": a_["choice"], "p": a_["probabilities"].get("불일치", 0), "text": text})
    write(rows, typo_rows, pages, base)


def write(rows, typo_rows, pages, base):
    cfg = config.settings()["context"]
    names = {**CONDS, "user": "직접 찍은 사진"}
    L = ["# 시연 사례 견고성 테스트", "",
         f"- 실행: {datetime.now():%Y-%m-%d %H:%M} / 실서비스 경로: 사진 축소({config.settings()['notice']['max_side']}px) → "
         f"Gemini 안내문 읽기 → Gemini 페이지 판별 → Jev 목적 비교 / 기준: 강한 {cfg['mismatch_strong']}, 확인 권장 {cfg['mismatch_check']}",
         f"- 시연 포스터 5장(docs/시연_시나리오.md) × 촬영 조건 {len(CONDS)}가지" +
         (f" + 직접 찍은 사진 {sum(r['cond'] == 'user' for r in rows)}장" if any(r["cond"] == "user" for r in rows) else ""),
         "- 페이지 내용은 demo_site/*.html 을 직접 읽어 넣음 (배포 서버 접속 대신)", "",
         "## 페이지 판별 결과 (Gemini)", "", "| 가짜 페이지 | AI가 본 페이지 목적 | 요구 정보 |", "|---|---|---|"]
    for pg, (page, ai_res) in pages.items():
        L.append(f"| {pg} | {(ai_res or {}).get('page_purpose', '실패')} | {', '.join((ai_res or {}).get('requested_info', []))} |")

    ok = [r for r in rows if "cer" in r]
    L += ["", "## 1. 촬영 조건별 OCR·판정", "",
          "| 촬영 조건 | 한국어 인식률 | 안내 목적 정확도 | 스티커 판정 정확도 | **목적 일치 판정 정확도** | 판정 실패 |",
          "|---|---|---|---|---|---|"]
    for cond in [*CONDS, "user"]:
        rs = [r for r in rows if r["cond"] == cond]
        if not rs:
            continue
        o = [r for r in rs if "cer" in r]
        L.append(f"| {names[cond]} | {(1 - sum(r['cer'] for r in o) / max(1, len(o))) * 100:.1f}% "
                 f"| {pct(sum(r['purpose_ok'] for r in o), len(o))} | {pct(sum(r['sticker_pred'] == r['sticker'] for r in o), len(o))} "
                 f"| **{pct(sum(r['decision'] == EXPECT[r['n']] for r in rs), len(rs))}** | {sum(r['status'] != 'ok' for r in rs)} |")
    L.append(f"| **전체** | {(1 - sum(r['cer'] for r in ok) / max(1, len(ok))) * 100:.1f}% | {pct(sum(r['purpose_ok'] for r in ok), len(ok))} "
             f"| {pct(sum(r['sticker_pred'] == r['sticker'] for r in ok), len(ok))} "
             f"| **{pct(sum(r['decision'] == EXPECT[r['n']] for r in rows), len(rows))}** | {sum(r['status'] != 'ok' for r in rows)} |")

    L += ["", "### 사례별 불일치 확률 (조건별)", "", "| 사례 | 기대 | " + " | ".join(names[c] for c in [*CONDS, "user"]
          if any(r["cond"] == c for r in rows)) + " |", "|---|---|" + "---|" * len({r["cond"] for r in rows})]
    for n in sorted({r["n"] for r in rows}):
        cells = []
        for c in [*CONDS, "user"]:
            if not any(r["cond"] == c for r in rows):
                continue
            r = next((r for r in rows if r["n"] == n and r["cond"] == c), None)
            if r is None:
                cells.append("")
            elif r["status"] != "ok":
                cells.append(f"실패({r['msg'][:15]})")
            else:
                mark = "" if r["decision"] == EXPECT[n] else "❌ "
                cells.append(f"{mark}{r['p']:.2f}" + (" 🔖" if r.get("sticker_pred") else ""))
        L.append(f"| {n} | {EXPECT[n]} | " + " | ".join(cells) + " |")
    L.append("\n🔖 = 스티커 의심으로 읽음")

    bad_ocr = sorted([r for r in ok if r["cer"] > 0.02], key=lambda r: -r["cer"])[:6]
    if bad_ocr:
        L += ["", "### 글자를 틀리게 읽은 예", ""]
        for r in bad_ocr:
            L += [f"**사례 {r['n']} · {names[r['cond']]} (CER {r['cer'] * 100:.0f}%)**", "```", r["text"], "```"]

    L += ["", "## 2. Jev 오타 내성 (시연 사례, 실서비스 질문 문구)", "",
          "| 오타 비율 | 실제 글자 오류율 | 판정 정확도 | 깨끗한 글자 대비 판정 바뀜 | 불일치 확률 변화 평균 | 최대 | 강한 기준(0.8) 넘나든 경우 |",
          "|---|---|---|---|---|---|---|"]
    clean = {r["n"]: r for r in typo_rows if r["rate"] == 0}
    for rate in sorted({r["rate"] for r in typo_rows}):
        rs = [r for r in typo_rows if r["rate"] == rate]
        flips = sum(r["decision"] != clean[r["n"]]["decision"] for r in rs)
        d = [abs(r["p"] - clean[r["n"]]["p"]) for r in rs]
        cross = sum((r["p"] >= cfg["mismatch_strong"]) != (clean[r["n"]]["p"] >= cfg["mismatch_strong"]) for r in rs)
        L.append(f"| {rate * 100:.0f}% | {sum(r['cer'] for r in rs) / len(rs) * 100:.1f}% "
                 f"| {pct(sum(r['decision'] == EXPECT[r['n']] for r in rs), len(rs))} | {pct(flips, len(rs))} "
                 f"| {sum(d) / len(d):.3f} | {max(d):.2f} | {cross} |")
    worst = max((r for r in typo_rows if r["rate"] > 0), key=lambda r: r["cer"], default=None)
    if worst:
        L += ["", f"가장 심한 오타 예 (사례 {worst['n']}, 오류율 {worst['cer'] * 100:.0f}%, 판정 {worst['decision']} {worst['p']:.2f}):",
              "```", worst["text"], "```"]
    out = ROOT / "eval" / "demo_eval_results.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"결과: {out}")


if __name__ == "__main__":
    main()
