"""피싱 점수 계산 (요구사항 4-1 ~ 4-4).

- 확정 신호: 점수와 상관없이 '위험'
- 강한 신호: 높은 배점
- 약한 신호: 낮은 배점, 합계 상한(weak_cap). 약한 신호만으로는 최대 '경고'
- 안전 신호: 감점. 확정 신호는 상쇄하지 못함
- 조합 규칙: 함께 나타나면 위험한 신호 조합에 추가 점수
- 중복 묶음(overlaps): 같은 사실을 규칙·AI가 함께 잡으면 점수가 큰 하나만 반영
배점·임계값·조합 규칙은 모두 config/scoring.json 에서 읽는다.
"""
from . import config

VERDICT_LABEL = {"safe": "안전", "caution": "경고", "danger": "위험"}


def make_signal(signal_id: str, detail: str, source: str = "규칙") -> dict:
    spec = config.scoring()["signals"][signal_id]
    return {"id": signal_id, "tier": spec["tier"], "points": spec["points"],
            "label": spec["label"], "detail": detail, "source": source}


def score(signals: list[dict]) -> dict:
    cfg = config.scoring()
    present = {s["id"] for s in signals}
    signals = list(signals)
    for combo in cfg["combos"]:
        if all(cond in present for cond in combo["when"]):
            signals.append({"id": combo["id"], "tier": combo["tier"], "points": combo["points"],
                            "label": combo["label"], "detail": combo["detail"], "source": "조합 규칙"})

    # 중복 묶음: 함께 걸린 신호 중 점수가 가장 큰 것(같으면 앞에 적힌 것)만 남기고 나머지는 0점 처리
    for group in cfg.get("overlaps", []):
        hits = [s for s in signals if s["id"] in group["signals"]]
        if len(hits) < 2:
            continue
        keep = max(hits, key=lambda s: (s["points"], -group["signals"].index(s["id"])))
        signals = [s if s is keep or s not in hits else
                   {**s, "points": 0, "detail": f"{s['detail']} ({group['note']})"} for s in signals]

    by_tier = {t: [s for s in signals if s["tier"] == t] for t in ("confirmed", "strong", "weak", "safe")}
    strong = sum(s["points"] for s in by_tier["strong"])
    weak_raw = sum(s["points"] for s in by_tier["weak"])
    weak = min(weak_raw, cfg["weak_cap"])
    safe = sum(s["points"] for s in by_tier["safe"])
    total = max(0, strong + weak - safe)

    th = cfg["thresholds"]
    if by_tier["confirmed"]:
        verdict = "danger"
    elif total >= th["danger"]:
        verdict = "danger"
    elif total >= th["caution"]:
        verdict = "caution"
    else:
        verdict = "safe"
    if verdict == "danger" and not by_tier["strong"] and not by_tier["confirmed"]:
        verdict = "caution"  # 약한 신호만으로는 최대 '경고'

    # 영향이 큰 순서: 확정 > 강한(점수 큰 순) > 약한(점수 큰 순)
    risk = by_tier["confirmed"] + sorted(by_tier["strong"], key=lambda s: -s["points"]) \
        + sorted(by_tier["weak"], key=lambda s: -s["points"])
    return {
        "verdict": verdict,
        "score": total,
        "breakdown": {"strong": strong, "weak": weak, "weak_raw": weak_raw, "weak_cap": cfg["weak_cap"],
                      "safe": safe, "thresholds": th},
        "risk_reasons": risk,
        "safe_reasons": sorted(by_tier["safe"], key=lambda s: -s["points"]),
    }
