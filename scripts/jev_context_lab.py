"""Jev 맥락 일치 실험실: 질문 문구 비교 + 오타 내성 테스트.

prompts : 질문 문구 후보(V1~V5)를 같은 짝(기본 39 + 애매한 16)에 물어 정확도·확률 여유폭을 비교
          → eval/jev_prompt_results.md
typos   : 안내문 글자에 OCR식 오타를 일부러 넣어(5·10·20·30%) 깨끗한 글자일 때와 판정·확률이 얼마나 달라지는지 측정.
          실제 OCR 결과(빛 반사 사진을 Gemini가 읽은 글자)도 함께 비교 → eval/jev_typo_results.md

사용법:  python scripts/jev_context_lab.py prompts
         python scripts/jev_context_lab.py typos --variant V1
응답은 data/ai_cache/jev/ 에 저장돼 다시 돌리면 비용 없이 재사용된다.
"""
import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import config, jev  # noqa: E402
import ocr_test  # noqa: E402
import jev_test  # noqa: E402

PRICE = 0.042  # 입력 100만 토큰당 달러

# ───────────────────────── 질문 문구 후보 ─────────────────────────
RULE_KO = jev_test.MATCH_RULE
CRIT_KO = jev_test.MATCH_CRITERIA
RULE_EN = ("Decide whether the purpose stated on the QR notice matches the purpose and requested information of the web page "
           "opened by scanning the QR. Treat as a mismatch: login to a different service than the notice, requests for "
           "sensitive information unnecessary for the notice's purpose (resident registration number, passwords, card CVC, "
           "account PIN, etc.), or requests to install an app file (apk) directly.")
CRIT_EN = {"일치": "The page does what the notice says and asks only for information needed for it",
           "불일치": "The page does something else, or asks for sensitive info or app installs unnecessary for the notice's purpose",
           "판단불가": "Not enough information to decide"}
RULE_NATURAL = (" 안내 목적에 자연스럽게 따라오는 절차(간단한 회원가입, 영수증·당첨 연락용 연락처, 기념품 배송 주소, "
                "위탁 운영사의 결제 대행, 공식 앱 마켓 링크)는 일치로 본다. 안내문에 없던 결제를 새로 요구하면 불일치로 본다.")

VARIANTS = {
    "V1": ("상세 한국어 (지금 쓰는 문구)", {"type": "choice", "instructions": RULE_KO, "criteria": CRIT_KO}),
    "V2": ("짧은 한국어", {"type": "choice", "instructions": "안내문 목적과 접속한 페이지 목적이 일치하는가?",
                          "criteria": {"일치": None, "불일치": None, "판단불가": None}}),
    "V3": ("영어 지시 + 한국어 데이터", {"type": "choice", "instructions": RULE_EN, "criteria": CRIT_EN}),
    "V4": ("상세 한국어 + 허용 절차 명시", {"type": "choice", "instructions": RULE_KO + RULE_NATURAL, "criteria": CRIT_KO}),
    "V5": ("예/아니오 확률(noul)", {"type": "noul", "instructions": RULE_KO + " 이 페이지는 안내문과 불일치하는가?"}),
}


def p_mismatch(variant: str, ans: dict) -> float:
    """답을 '불일치 확률' 하나로 통일 (choice: 불일치 확률, noul: 예 확률)."""
    return ans["noul"] if ans["type"] == "noul" else ans["probabilities"].get("불일치", 0.0)


def decision(variant: str, ans: dict) -> str:
    if ans["type"] == "noul":
        return "불일치" if ans["noul"] >= 0.5 else "일치"
    return ans["choice"]


# ───────────────────────── 짝 목록 ─────────────────────────

def all_pairs() -> list[dict]:
    out = [{**p, "set": "기본"} for p in jev_test.pairs()]
    for h in jev_test.CTX.get("hard", []):
        out.append({"case": h["case"], "kind": "애매한 짝", "label": h["label"], "page": h["page"], "why": h["why"],
                    "set": "애매"})
    return out


def notice_text(case_id: str) -> str:
    case = next(c for c in ocr_test.CASES["cases"] if c["id"] == case_id)
    return ocr_test.truth_text(case)


def state_of(text: str, page: dict) -> dict:
    return {"QR 안내문": text, "접속한 페이지": jev_test._page_text(page)}


def run_many(jobs: list[tuple]) -> list[dict]:
    """(state, question) 목록을 동시에 4개씩 Jev에 묻는다."""
    def one(job):
        state, q = job
        return jev.ask(state, {"match": q})
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, jobs))


def pct(n, d):
    return f"{n / d * 100:.0f}% ({n}/{d})" if d else "-"


# ───────────────────────── 1. 질문 문구 비교 ─────────────────────────

def cmd_prompts() -> None:
    pairs = all_pairs()
    rows = {}
    for v, (name, q) in VARIANTS.items():
        res = run_many([(state_of(notice_text(p["case"]), p["page"]), q) for p in pairs])
        rows[v] = list(zip(pairs, res))
        ok = sum(r["status"] == "ok" for r in res)
        print(f"{v} {name}: 성공 {ok}/{len(res)}")

    L = ["# Jev 질문 문구 비교", "",
         f"- 실행: {datetime.now():%Y-%m-%d %H:%M} / 모델: {config.settings()['jev']['model']}",
         f"- 짝: 기본 {sum(p['set'] == '기본' for p in pairs)}개 (지난 테스트) + 애매한 짝 {sum(p['set'] == '애매' for p in pairs)}개 "
         "(eval/context_cases.json 의 hard)",
         "- 안내문은 정답 글자(깨끗한 텍스트), 페이지는 AI 판별 결과와 같은 형식의 요약",
         "- 확률 여유폭 = 정답 쪽에 준 확률의 평균 (1에 가까울수록 확실하게 맞힘). 최저 여유폭 = 가장 아슬아슬했던 짝", "",
         "| 문구 | 설명 | 기본 정확도 | **애매한 짝 정확도** | 평균 여유폭 | 최저 여유폭 | '판단불가' 답 | 1회 입력 토큰 |",
         "|---|---|---|---|---|---|---|---|"]
    summary = {}
    for v, (name, _) in VARIANTS.items():
        ok = [(p, r) for p, r in rows[v] if r["status"] == "ok"]
        dec = [(p, decision(v, r["answers"]["match"]), p_mismatch(v, r["answers"]["match"])) for p, r in ok]
        base = [x for x in dec if x[0]["set"] == "기본"]
        hard = [x for x in dec if x[0]["set"] == "애매"]
        margin = [pm if p["label"] == "불일치" else 1 - pm for p, _, pm in dec]
        unsure = sum(d == "판단불가" for _, d, _ in dec)
        tok = sum(r["input_tokens"] for _, r in ok) / max(1, len(ok))
        acc_h = sum(d == p["label"] for p, d, _ in hard) / max(1, len(hard))
        summary[v] = (acc_h, sum(margin) / max(1, len(margin)))
        L.append(f"| {v} | {name} | {pct(sum(d == p['label'] for p, d, _ in base), len(base))} "
                 f"| **{pct(sum(d == p['label'] for p, d, _ in hard), len(hard))}** "
                 f"| {sum(margin) / max(1, len(margin)):.2f} | {min(margin, default=0):.2f} | {unsure} | {tok:.0f} |")
    best = max(summary, key=lambda v: summary[v])
    L += ["", f"**가장 좋은 문구: {best} ({VARIANTS[best][0]})** (애매한 짝 정확도 → 평균 여유폭 순)", "",
          "> V4 는 애매한 짝의 정답 기준(허용 절차)을 지시문에 적은 것이라 이 짝들에 유리하다. "
          "실제 사진·사례로 다시 확인해야 한다.", "",
          "## 애매한 짝 판정 상세 (불일치 확률)", "",
          "| 안내문 | 페이지 목적 | 정답 | 이유 | " + " | ".join(VARIANTS) + " |",
          "|---|---|---|---|" + "---|" * len(VARIANTS)]
    hard_pairs = [p for p in pairs if p["set"] == "애매"]
    for i, p in enumerate(hard_pairs):
        cells = []
        for v in VARIANTS:
            r = [r for pp, r in rows[v] if pp is p][0]
            if r["status"] != "ok":
                cells.append("실패")
                continue
            d, pm = decision(v, r["answers"]["match"]), p_mismatch(v, r["answers"]["match"])
            cells.append(("" if d == p["label"] else "❌ ") + f"{d} {pm:.2f}")
        L.append(f"| {p['case']} | {p['page']['page_purpose']} | {p['label']} | {p['why']} | " + " | ".join(cells) + " |")
    wrong_base = [(v, p) for v in VARIANTS for p, r in rows[v]
                  if p["set"] == "기본" and r["status"] == "ok" and decision(v, r["answers"]["match"]) != p["label"]]
    if wrong_base:
        L += ["", "## 기본 짝에서 틀린 경우", ""] + [f"- {v}: {p['case']} / {p['page']['page_purpose']} (정답 {p['label']})"
                                                   for v, p in wrong_base]
    toks = sum(r["input_tokens"] for v in rows for _, r in rows[v] if r["status"] == "ok" and not r.get("cached"))
    L += ["", f"> 이번 실행 새 호출 입력 토큰 {toks:,}개 ≈ ${toks / 1e6 * PRICE:.4f}"]
    out = ROOT / "eval" / "jev_prompt_results.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"가장 좋은 문구: {best} / 결과: {out}")


# ───────────────────────── 2. 오타 내성 ─────────────────────────

_V_SWAP = {1: 5, 5: 1, 4: 6, 6: 4, 8: 12, 12: 8, 13: 17, 17: 13, 0: 2, 2: 0}   # ㅐ↔ㅔ ㅓ↔ㅕ ㅗ↔ㅛ ㅜ↔ㅠ ㅏ↔ㅑ
_L_SWAP = {5: 3, 3: 5, 6: 11, 11: 6, 0: 15, 15: 0, 7: 17, 17: 7}             # ㄹ↔ㄷ ㅁ↔ㅇ ㄱ↔ㅋ ㅂ↔ㅍ
_ASCII = {"0": "O", "O": "0", "1": "l", "l": "1", "5": "S", "S": "5", "8": "B", "B": "8", ",": ".", ".": ","}


def _hangul(ch: str) -> tuple[int, int, int] | None:
    c = ord(ch) - 0xAC00
    return (c // 588, (c % 588) // 28, c % 28) if 0 <= c < 11172 else None


def _compose(l: int, v: int, t: int) -> str:
    return chr(0xAC00 + l * 588 + v * 28 + t)


def add_typos(text: str, rate: float, seed: int) -> str:
    """OCR이 흔히 내는 오류를 글자마다 rate 확률로 넣는다.
    한글: 모음 혼동(ㅐ↔ㅔ 등), 받침 빠짐, 자음 혼동(ㄹ↔ㄷ 등), 글자 빠짐 / 숫자·영문: 0↔O, 1↔l 등 / 띄어쓰기 사라짐, 줄 합쳐짐"""
    rng = random.Random(seed)
    out = []
    for ch in text:
        if rng.random() >= rate:
            out.append(ch)
            continue
        h = _hangul(ch)
        if h:
            l, v, t = h
            op = rng.choice(["vowel", "final", "initial", "drop"])
            if op == "vowel" and v in _V_SWAP:
                out.append(_compose(l, _V_SWAP[v], t))
            elif op == "final" and t:
                out.append(_compose(l, v, 0))
            elif op == "initial" and l in _L_SWAP:
                out.append(_compose(_L_SWAP[l], v, t))
            elif op == "drop":
                pass
            else:
                out.append(_compose(l, v, 0) if t else _compose(l, _V_SWAP.get(v, v), t))
        elif ch in _ASCII:
            out.append(_ASCII[ch])
        elif ch == " ":
            pass                       # 띄어쓰기 사라짐
        elif ch == "\n":
            out.append(" ")            # 줄 합쳐짐
        else:
            out.append(ch)
    return "".join(out)


RATES = [0.05, 0.10, 0.20, 0.30]
SEEDS = [1, 2, 3]


def real_ocr_text(case_id: str, cond: str) -> str | None:
    model = config.settings()["ai"]["model"]
    raw = ocr_test.RAW_DIR / f"{model}__v{ocr_test.PROMPT_VERSION}__ocr_samples__{case_id}__{cond}.json"
    return json.loads(raw.read_text(encoding="utf-8"))["result"]["full_text"] if raw.exists() else None


def cmd_typos(variant: str) -> None:
    name, q = VARIANTS[variant]
    pairs = all_pairs()
    texts = {}  # (조건, seed) -> {case: text}
    cases = sorted({p["case"] for p in pairs})
    texts[("깨끗한 글자", 0)] = {c: notice_text(c) for c in cases}
    for rate in RATES:
        for s in SEEDS:
            texts[(f"오타 {rate * 100:.0f}%", s)] = {c: add_typos(notice_text(c), rate, seed=s * 1000 + i)
                                                   for i, c in enumerate(cases)}
    real = {c: real_ocr_text(c, "glare") for c in cases}
    if all(real.values()):
        texts[("실제 OCR (빛 반사 사진)", 0)] = real

    jobs, keys = [], []
    for k, tx in texts.items():
        for i, p in enumerate(pairs):
            jobs.append((state_of(tx[p["case"]], p["page"]), q))
            keys.append((k, i))
    print(f"문구 {variant}, Jev 호출 {len(jobs)}건 (저장된 응답은 재사용)")
    res = run_many(jobs)
    got = {key: r for key, r in zip(keys, res)}

    clean = {i: got[(("깨끗한 글자", 0), i)] for i in range(len(pairs))}
    L = ["# Jev 오타 내성 테스트", "",
         f"- 실행: {datetime.now():%Y-%m-%d %H:%M} / 질문 문구: {variant} ({name}) / 짝 {len(pairs)}개 (기본 + 애매한 짝)",
         "- 안내문 글자에 OCR식 오타를 일부러 넣고, 깨끗한 글자일 때와 판정이 바뀌는지·불일치 확률이 얼마나 흔들리는지 본다.",
         "- 오타 종류: 한글 모음 혼동(ㅐ↔ㅔ 등)·받침 빠짐·자음 혼동(ㄹ↔ㄷ 등)·글자 빠짐, 0↔O·1↔l, 띄어쓰기 사라짐, 줄 합쳐짐",
         f"- 오타 비율마다 무작위 seed {len(SEEDS)}개로 반복. 실제 글자 오류율(CER)은 넣은 오타를 다시 재서 표시", "",
         "| 안내문 글자 | 실제 글자 오류율 | 정확도 | 깨끗한 글자 대비 판정 바뀜 | 불일치 확률 변화 (평균) | 변화 (최대) |",
         "|---|---|---|---|---|---|"]
    order = list(dict.fromkeys(k[0] for k in texts))
    examples = {}
    for cond in order:
        ks = [k for k in texts if k[0] == cond]
        n = acc = flip = 0
        deltas, cers = [], []
        for k in ks:
            for c in cases:
                cers.append(ocr_test.cer(texts[k][c], notice_text(c)))
            for i, p in enumerate(pairs):
                r, c0 = got[(k, i)], clean[i]
                if r["status"] != "ok" or c0["status"] != "ok":
                    continue
                a, a0 = r["answers"]["match"], c0["answers"]["match"]
                d, d0 = decision(variant, a), decision(variant, a0)
                n += 1
                acc += d == p["label"]
                flip += d != d0
                delta = abs(p_mismatch(variant, a) - p_mismatch(variant, a0))
                deltas.append(delta)
                if d != d0 and cond not in examples:
                    examples[cond] = (p, texts[k][p["case"]], d0, d, p_mismatch(variant, a0), p_mismatch(variant, a))
        L.append(f"| {cond} | {sum(cers) / len(cers) * 100:.1f}% | {pct(acc, n)} | {pct(flip, n)} "
                 f"| {sum(deltas) / max(1, len(deltas)):.3f} | {max(deltas, default=0):.2f} |")

    L += ["", "## 오타 예시 (주차장 안내문)", ""]
    for cond in order:
        k = next(k for k in texts if k[0] == cond)
        L += [f"**{cond}**", "", "```", texts[k]["parking"], "```", ""]
    if examples:
        L += ["## 판정이 바뀐 예", "", "| 조건 | 안내문 | 페이지 목적 | 정답 | 깨끗한 글자 → 오타 글자 |", "|---|---|---|---|---|"]
        for cond, (p, t, d0, d, p0, p1) in examples.items():
            L.append(f"| {cond} | {p['case']} | {p['page']['page_purpose']} | {p['label']} | {d0} ({p0:.2f}) → {d} ({p1:.2f}) |")
    toks = sum(r["input_tokens"] for r in res if r["status"] == "ok" and not r.get("cached"))
    L += ["", f"> 이번 실행 새 호출 입력 토큰 {toks:,}개 ≈ ${toks / 1e6 * PRICE:.4f}"]
    out = ROOT / "eval" / "jev_typo_results.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"결과: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Jev 맥락 일치 실험실")
    ap.add_argument("cmd", choices=["prompts", "typos"])
    ap.add_argument("--variant", default="V1", choices=list(VARIANTS))
    a = ap.parse_args()
    if not jev.available():
        sys.exit("TYPESAFE_API_KEY 가 .env 에 없습니다.")
    cmd_prompts() if a.cmd == "prompts" else cmd_typos(a.variant)


if __name__ == "__main__":
    main()
