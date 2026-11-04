"""임계값 검증 스크립트 (요구사항 4-4).

수집해 둔 저장본(data/snapshots)으로 판정 성능을 측정한다. 실시간 접속을 하지 않으므로 다시 돌려도 같은 결과가 나온다.
(AI 응답도 data/ai_cache 에 저장해 재사용)

비교하는 설정
  A. 규칙만          : URL·도메인·페이지 규칙 (피싱 DB·신뢰 목록 제외)
  B. 규칙 + AI       : A + AI 사칭 판별  → AI가 더한 효과
  C. 실서비스 설정   : B + 피싱 DB + 신뢰 사이트 목록
피싱 URL은 OpenPhish 피드에서 왔으므로 C에서는 DB 등재로 거의 다 잡힌다. 규칙과 AI의 실력은 A·B로 본다.

사용법:  python scripts/evaluate.py          (AI 포함, 처음엔 분당 요청 한도 때문에 오래 걸림)
         python scripts/evaluate.py --no-ai  (AI 제외, 빠름)
결과:    eval/results.md (발표용 표), data/eval_samples.csv (URL별 상세)
"""
import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.analyzer import analyze  # noqa: E402

SNAP_DIR = config.DATA_DIR / "snapshots"
MODES = {
    "A": ("규칙만", dict(use_db=False, use_whitelist=False, use_ai=False)),
    "B": ("규칙 + AI", dict(use_db=False, use_whitelist=False, use_ai=True)),
    "C": ("실서비스 설정 (DB+신뢰목록+AI)", dict(use_db=True, use_whitelist=True, use_ai=True)),
}
BINS = [(0, 0), (1, 29), (30, 59), (60, 89), (90, 10_000)]


def load_snapshots():
    snaps = []
    for path in sorted(SNAP_DIR.glob("*/*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            snaps.append(json.load(f))
    return snaps


def verdict_at(row: dict, caution: int, danger: int) -> str:
    """임계값을 바꿨을 때의 판정 (scoring.score 와 같은 규칙)."""
    if row["confirmed"]:
        return "danger"
    v = "danger" if row["score"] >= danger else "caution" if row["score"] >= caution else "safe"
    if v == "danger" and not row["has_strong"]:
        v = "caution"
    return v


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}% ({n}/{d})" if d else "-"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ai", action="store_true", help="AI 설정(B, C의 AI)을 빼고 빠르게 실행")
    args = parser.parse_args()

    snaps = load_snapshots()
    labels = Counter(s["label"] for s in snaps)
    print(f"저장본 {len(snaps)}개 {dict(labels)}")
    th = config.scoring()["thresholds"]

    rows = {k: [] for k in MODES}
    ai_stats = Counter()
    for i, snap in enumerate(snaps, 1):
        for key, (_, opts) in MODES.items():
            opts = dict(opts)
            if args.no_ai:
                opts["use_ai"] = False
            r = analyze(snap, ai_wait=True, **opts)
            if key == "B":
                ai_stats[r["ai"]["status"]] += 1
            rows[key].append({
                "url": snap["url"], "label": snap["label"], "verdict": r["verdict"], "score": r["score"],
                "confirmed": any(x["tier"] == "confirmed" for x in r["risk_reasons"]),
                "has_strong": any(x["tier"] == "strong" for x in r["risk_reasons"]),
                "signals": "|".join(x["id"] for x in r["risk_reasons"]),
                "ai": r["ai"]["message"],
            })
        if i % 10 == 0:
            print(f"  {i}/{len(snaps)} 처리 (AI: {dict(ai_stats)})", flush=True)

    lines = [f"# 판정 성능 검증 결과", "",
             f"- 실행: {datetime.now():%Y-%m-%d %H:%M}",
             f"- 데이터: 피싱 {labels['phishing']}개 (OpenPhish), 정상 {labels['benign']}개 (Tranco 상위 + 국내 주요 사이트·로그인 페이지)",
             f"- 임계값: 경고 {th['caution']}점, 위험 {th['danger']}점 이상 / AI 모델: {config.settings()['ai']['model']}"
             + (" / **AI 제외 실행**" if args.no_ai else ""), "",
             "## 1. 설정별 성능", "",
             "| 설정 | 피싱 탐지율 (위험) | 피싱 탐지율 (경고 이상) | 정상 오탐률 (위험) | 정상 오탐률 (경고 이상) |",
             "|---|---|---|---|---|"]
    for key, (name, _) in MODES.items():
        ph = [r for r in rows[key] if r["label"] == "phishing"]
        bn = [r for r in rows[key] if r["label"] == "benign"]
        lines.append(f"| {key}. {name} | {pct(sum(r['verdict'] == 'danger' for r in ph), len(ph))} "
                     f"| {pct(sum(r['verdict'] != 'safe' for r in ph), len(ph))} "
                     f"| {pct(sum(r['verdict'] == 'danger' for r in bn), len(bn))} "
                     f"| {pct(sum(r['verdict'] != 'safe' for r in bn), len(bn))} |")

    lines += ["", "## 2. 점수 분포 (B. 규칙 + AI)", "", "| 점수 구간 | 피싱 | 정상 |", "|---|---|---|"]
    for lo, hi in BINS:
        name = f"{lo}점" if lo == hi else (f"{lo}점 이상" if hi > 1000 else f"{lo}~{hi}점")
        cnt = {lab: sum(lo <= r["score"] <= hi for r in rows["B"] if r["label"] == lab) for lab in ("phishing", "benign")}
        lines.append(f"| {name} | {cnt['phishing']} | {cnt['benign']} |")

    lines += ["", "## 3. 위험 임계값별 비교 (B. 규칙 + AI, 경고 임계값은 고정)", "",
              "| 위험 임계값 | 피싱 탐지율 (위험) | 정상 오탐률 (위험) | 정상 오탐률 (경고 이상) |", "|---|---|---|---|"]
    for danger in (40, 50, 60, 70, 80):
        caution = min(th["caution"], danger)
        ph = [verdict_at(r, caution, danger) for r in rows["B"] if r["label"] == "phishing"]
        bn = [verdict_at(r, caution, danger) for r in rows["B"] if r["label"] == "benign"]
        mark = " (현재)" if danger == th["danger"] else ""
        lines.append(f"| {danger}점{mark} | {pct(ph.count('danger'), len(ph))} | {pct(bn.count('danger'), len(bn))} "
                     f"| {pct(sum(v != 'safe' for v in bn), len(bn))} |")

    sig = {lab: Counter(s for r in rows["B"] if r["label"] == lab for s in r["signals"].split("|") if s)
           for lab in ("phishing", "benign")}
    lines += ["", "## 4. 신호별 발생 횟수 (B. 규칙 + AI)", "", "| 신호 | 피싱 | 정상 |", "|---|---|---|"]
    spec = config.scoring()["signals"]
    combos = {c["id"]: c["label"] for c in config.scoring()["combos"]}
    for s in sorted(set(sig["phishing"]) | set(sig["benign"]), key=lambda s: -sig["phishing"][s]):
        name = spec[s]["label"] if s in spec else combos.get(s, s)
        lines.append(f"| {name} | {sig['phishing'][s]} | {sig['benign'][s]} |")

    lines += ["", f"AI 호출 결과 (B): {dict(ai_stats)}", "",
              "> 참고: 정상 사이트는 대부분 홈페이지라 로그인 폼이 적고, 피싱 사이트는 수집 시점에 이미 차단된 곳이 많아",
              "> 실제 환경과 차이가 있을 수 있다. 피싱 DB 효과(C)는 피싱 URL을 같은 피드에서 가져왔으므로 과대평가되어 있다."]

    out = ROOT / "eval" / "results.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (config.DATA_DIR / "eval_samples.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["mode", *rows["A"][0].keys()])
        writer.writeheader()
        for key, rs in rows.items():
            for r in rs:
                writer.writerow({"mode": key, **r})
    print("\n".join(lines))
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
