"""판정 위계: 범죄유형 > 예방 수칙(대분류) > 점수 신호(소분류).

점수·판정은 scoring.py 가 그대로 계산한다. 이 모듈은 그 결과를
  - 예방 수칙별로 묶고 (groups)
  - 결과 첫 화면에 보여 줄 대표 수칙 한 줄(top_rule)과 행동 안내(action)를 고른다.
위계·문구는 config/rules_hierarchy.json 에서 읽는다.
"""
from . import config

DEFAULT_CRIME = "phishing"


def crime_type(crime_id: str = DEFAULT_CRIME) -> dict:
    for ct in config.hierarchy()["crime_types"]:
        if ct["id"] == crime_id:
            return ct
    raise KeyError(f"rules_hierarchy.json 에 범죄유형 '{crime_id}' 없음")


def mapping_problems(crime_id: str = DEFAULT_CRIME) -> list[str]:
    """scoring.json 의 모든 신호·조합 규칙이 정확히 한 수칙에 들어 있는지 검사한다. (테스트용)"""
    cfg = config.scoring()
    known = set(cfg["signals"]) | {c["id"] for c in cfg["combos"]}
    seen: dict[str, str] = {}
    problems = []
    for p in crime_type(crime_id)["principles"]:
        for sid in p["signals"]:
            if sid not in known:
                problems.append(f"'{p['id']}'의 신호 '{sid}'가 scoring.json 에 없음")
            if sid in seen:
                problems.append(f"신호 '{sid}'가 '{seen[sid]}'와 '{p['id']}' 두 곳에 있음")
            seen[sid] = p["id"]
    problems += [f"신호 '{sid}'가 어느 수칙에도 없음" for sid in sorted(known - set(seen))]
    return problems


def _principle_of(ct: dict, item_id: str, key: str) -> dict | None:
    return next((p for p in ct["principles"] if item_id in p[key]), None)


def _signal_item(s: dict) -> dict:
    return {"type": "signal", "id": s["id"], "tier": s["tier"], "points": s["points"], "label": s["label"],
            "detail": s["detail"], "source": s["source"]}


def _notice_item(n: dict) -> dict:
    return {"type": "notice", "id": n["id"], "severe": bool(n.get("escalate")), "detail": n["detail"],
            "source": "DB" if n.get("escalate") else "규칙"}


def build(*, verdict: str, phishing_verdict: str, risk_reasons: list[dict], safe_reasons: list[dict],
          notices: list[dict], crime_id: str = DEFAULT_CRIME) -> dict:
    """판정 결과를 위계에 맞춰 정리한다. risk_reasons 는 영향이 큰 순서(scoring.score 결과)여야 한다."""
    ct = crime_type(crime_id)
    groups = {p["id"]: {"id": p["id"], "kind": p["kind"], "title": p["title"], "items": [],
                        **({"phase2_note": p["phase2_note"]} if p.get("phase2_note") else {})}
              for p in ct["principles"]}
    other = {"id": "other", "kind": "risk", "title": "기타 근거", "items": []}

    for s in risk_reasons + safe_reasons:
        p = _principle_of(ct, s["id"], "signals")
        (groups[p["id"]] if p else other)["items"].append(_signal_item(s))
    for n in notices:
        p = _principle_of(ct, n["id"], "notices")
        g = groups[p["id"]] if p else other
        # 같은 뜻의 신호가 이미 있으면(예: http) 하나로 합친다. 설명은 더 자세한 주의사항 문구를 쓴다.
        same = next((i for i in g["items"] if i["type"] == "signal" and i["id"] == n["id"]), None)
        if same:
            same["detail"] = n["detail"]
        else:
            g["items"].append(_notice_item(n))

    # 대표 수칙: 악성코드 DB > 피싱 근거 중 영향이 가장 큰 것 > (안전 판정이면) 안전 안내가 있는 주의사항
    top = None
    severe = next((n for n in notices if n.get("escalate")), None)
    if severe:
        top = _principle_of(ct, severe["id"], "notices")
    elif phishing_verdict != "safe" and risk_reasons:
        top = _principle_of(ct, risk_reasons[0]["id"], "signals")
    else:
        for n in notices:
            p = _principle_of(ct, n["id"], "notices")
            if p and verdict in p.get("actions", {}):
                top = p
                break

    action = (top or {}).get("actions", {}).get(verdict) or ct["default_actions"][verdict]
    ordered = list(groups.values()) + ([other] if other["items"] else [])
    return {
        "crime_type": {"id": ct["id"], "name": ct["name"]},
        "top_rule": {"id": top["id"], "title": top["title"], "one_liner": top["one_liner"]} if top else None,
        "one_liner": top["one_liner"] if top else ct["safe_one_liner"],
        "action": action,
        "groups": ordered,
    }
