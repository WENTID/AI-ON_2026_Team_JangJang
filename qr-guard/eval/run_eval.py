"""baseline(URL만 검사) 과 우리 방식(LLM 판정) 비교 평가.

실행 (가짜 서버를 먼저 켜야 함):
    python eval/run_eval.py               # 정답셋 전체 1회
    python eval/run_eval.py --repeat 3    # 같은 사례를 3번 돌려 판정이 흔들리는지 확인
    python eval/run_eval.py --no-cache    # LLM 캐시를 쓰지 않음

결과: eval/results/<날짜_시간>/ 아래에 cases.csv, cases.md, summary.md, diff.md
"""

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline import analyze, load_scenarios  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "results"
MOCK_HEALTH_URL = "http://127.0.0.1:5001/health"
CATEGORY_KO = {"phishing": "피싱", "normal": "정상", "ambiguous": "애매"}


def server_is_up() -> bool:
    try:
        return httpx.get(MOCK_HEALTH_URL, timeout=2).status_code == 200
    except Exception:
        return False


def run_case(s: dict, repeat: int, use_cache: bool) -> dict:
    """시나리오 하나를 repeat 번 돌리고 대표 판정(최빈값)을 정한다."""
    verdicts, results = [], []
    for _ in range(repeat):
        r = analyze(url=s["qr_url"], notice_text=s.get("notice_text", ""), test_mode=True,
                    override=s.get("override"), use_cache=use_cache)
        verdicts.append(r["verdict"])
        results.append(r)
    ours = Counter(verdicts).most_common(1)[0][0]
    rep = results[verdicts.index(ours)]
    acceptable = s.get("acceptable_verdicts") or [s["expected_verdict"]]
    baseline = rep["baseline"]["verdict"] if rep["baseline"] else "SAFE"
    return {
        "id": s["id"],
        "category": s["category"],
        "title": s.get("title", ""),
        "expected": s["expected_verdict"],
        "acceptable": "/".join(acceptable),
        "baseline": baseline,
        "baseline_ok": baseline in acceptable,
        "ours": ours,
        "ours_ok": ours in acceptable,
        "unstable": len(set(verdicts)) > 1,
        "all_runs": ",".join(verdicts),
        "evidence": " / ".join(rep["evidence"]),
        "expected_points": " / ".join(s.get("expected_points", [])),
        "baseline_reasons": " / ".join(rep["baseline"]["reasons"]) if rep["baseline"] else "",
        "llm_error": (rep["llm"] or {}).get("error") or "",
    }


def _ratio(num: int, den: int) -> str:
    return f"{num / den:.0%} ({num}/{den})" if den else "N/A (0건)"


def metrics(df: pd.DataFrame, col: str) -> dict:
    """한 방식(col = 'baseline' 또는 'ours')의 5개 지표."""
    ok = df[f"{col}_ok"]
    ph = df[df.category == "phishing"]
    nm = df[df.category == "normal"]
    am = df[df.category == "ambiguous"]
    return {
        "정답률": _ratio(int(ok.sum()), len(df)),
        "피싱 탐지율 (DANGER+CAUTION)": _ratio(int(ph[col].isin(["DANGER", "CAUTION"]).sum()), len(ph)),
        "피싱 정확 탐지율 (DANGER)": _ratio(int((ph[col] == "DANGER").sum()), len(ph)),
        "정상 오탐률 (낮을수록 좋음)": _ratio(int(nm[col].isin(["DANGER", "CAUTION"]).sum()), len(nm)),
        "애매 사례 적절 처리율": _ratio(int(df.loc[am.index, f"{col}_ok"].sum()), len(am)),
    }


def md_table(headers: list, rows: list) -> str:
    """간단한 마크다운 표 (추가 패키지 없이)."""
    def esc(v):
        return str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def mark(ok: bool) -> str:
    return "O" if ok else "X"


def write_outputs(df: pd.DataFrame, out_dir: Path, args) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "cases.csv", index=False, encoding="utf-8-sig")  # 엑셀에서 한글 안 깨지게

    # 1) 사례별 결과표
    rows = [[r.id, CATEGORY_KO.get(r.category, r.category), r.acceptable, r.baseline, mark(r.baseline_ok),
             r.ours + (" ⚠흔들림" if r.unstable else ""), mark(r.ours_ok), r.evidence, r.expected_points]
            for r in df.itertuples()]
    (out_dir / "cases.md").write_text(
        "# 사례별 결과\n\n"
        "근거(evidence)가 기대 근거(expected_points)와 맞는지 사람이 직접 확인하세요.\n\n"
        + md_table(["ID", "유형", "정답(허용)", "baseline", "맞음", "우리", "맞음", "우리 근거", "기대 근거"], rows)
        + "\n", encoding="utf-8")

    # 2) 요약표
    mb, mo = metrics(df, "baseline"), metrics(df, "ours")
    rows = [[k, mb[k], mo[k]] for k in mb]
    counts = df.category.value_counts()
    info = (f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M}\n"
            f"- 사례 수: {len(df)} (피싱 {counts.get('phishing', 0)}, 정상 {counts.get('normal', 0)}, "
            f"애매 {counts.get('ambiguous', 0)})\n"
            f"- 반복 횟수: {args.repeat}\n")
    if args.repeat > 1:
        info += f"- 판정이 흔들린 사례: {', '.join(df[df.unstable].id) or '없음'}\n"
    errors = df[df.llm_error != ""]
    if len(errors):
        info += f"- ⚠ LLM 오류가 난 사례 {len(errors)}건: {', '.join(errors.id)} (예: {errors.iloc[0].llm_error})\n"
    (out_dir / "summary.md").write_text(
        "# 평가 요약\n\n" + info + "\n" + md_table(["지표", "URL만 검사 (baseline)", "우리 방식"], rows) + "\n",
        encoding="utf-8")

    # 3) 차이 사례
    better = df[~df.baseline_ok & df.ours_ok]
    worse = df[df.baseline_ok & ~df.ours_ok]
    both_wrong = df[~df.baseline_ok & ~df.ours_ok]

    def section(title, sub):
        if sub.empty:
            return f"## {title}\n\n없음\n\n"
        rows = [[r.id, r.title, r.acceptable, r.baseline, r.ours, r.evidence, r.baseline_reasons]
                for r in sub.itertuples()]
        return f"## {title} ({len(sub)}건)\n\n" + md_table(
            ["ID", "사례", "정답(허용)", "baseline", "우리", "우리 근거", "baseline 이유"], rows) + "\n\n"

    (out_dir / "diff.md").write_text(
        "# 차이 사례\n\n우리가 틀린 사례도 숨기지 않고 발표에 포함합니다.\n\n"
        + section("baseline은 틀리고 우리는 맞힌 사례", better)
        + section("baseline은 맞고 우리는 틀린 사례", worse)
        + section("둘 다 틀린 사례", both_wrong),
        encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="baseline 과 우리 방식 비교 평가")
    parser.add_argument("--repeat", type=int, default=1, help="사례당 반복 횟수 (기본 1)")
    parser.add_argument("--no-cache", action="store_true", help="LLM 캐시를 쓰지 않음")
    parser.add_argument("--scenarios", default=str(ROOT / "data" / "scenarios.json"))
    args = parser.parse_args()

    if not server_is_up():
        print("가짜 서버가 꺼져 있습니다. 다른 터미널에서 먼저 실행하세요:  python mock_site/server.py")
        return 1

    scenarios = load_scenarios(args.scenarios)
    if not scenarios:
        print(f"정답셋을 읽지 못했습니다: {args.scenarios}")
        return 1

    # 반복 실행 시에는 캐시를 끄고 매번 새로 호출해야 흔들림을 볼 수 있다
    use_cache = not args.no_cache and args.repeat == 1

    rows = []
    for i, s in enumerate(scenarios, 1):
        print(f"[{i}/{len(scenarios)}] {s['id']} {s.get('title', '')} ...", end=" ", flush=True)
        r = run_case(s, args.repeat, use_cache)
        rows.append(r)
        print(f"baseline={r['baseline']}({mark(r['baseline_ok'])})  우리={r['ours']}({mark(r['ours_ok'])})"
              + ("  ⚠흔들림" if r["unstable"] else ""))

    df = pd.DataFrame(rows)
    out_dir = RESULTS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    write_outputs(df, out_dir, args)

    print("\n" + (out_dir / "summary.md").read_text(encoding="utf-8"))
    if (df.llm_error != "").any():
        print("⚠ 일부 사례에서 LLM 오류가 났습니다. .env 의 API 키와 모델명을 확인하세요.")
    print(f"결과 저장: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
