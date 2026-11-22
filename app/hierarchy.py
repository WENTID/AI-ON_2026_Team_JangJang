"""판정 위계: 범죄유형 > 예방 수칙(대분류) > 검사 묶음(중분류) > 검사 항목(소분류) > 점수 신호.

점수·판정은 scoring.py 가 그대로 계산한다. 이 모듈은 그 결과를
  - 검사 항목마다 결과(위험/경고/안전/확인 불가/해당 없음/2단계)를 매겨 트리 형태 리포트(report)로 만들고
  - 결과 첫 화면에 보여 줄 대표 수칙 한 줄(top_rule)과 행동 안내(action)를 고른다.
위계·문구는 config/rules_hierarchy.json 에서 읽는다.
"""
from . import config

DEFAULT_CRIME = "phishing"
# 검사 항목 결과. caution 은 화면에서 '경고'로 표시한다.
# unknown(확인 불가) = 검사를 하려 했지만 실패(접속 실패·AI 한도 등). '안전'보다 위에 두어 대분류에서 실패가 가려지지 않게 한다.
# na(해당 없음) = 할 필요가 없던 검사(사진 없음, http라 인증서 없음 등).
# blank(-) = 단순 QR(안내문 없이 QR만 찍은 사진)이라 안내문 관련 검사 결과를 비워 둔 것.
RANK = {"danger": 3, "caution": 2, "unknown": 1.5, "safe": 1, "na": 0, "blank": 0, "phase2": 0}


def crime_type(crime_id: str = DEFAULT_CRIME) -> dict:
    for ct in config.hierarchy()["crime_types"]:
        if ct["id"] == crime_id:
            return ct
    raise KeyError(f"rules_hierarchy.json 에 범죄유형 '{crime_id}' 없음")


def _checks(p: dict):
    for sec in p["sections"]:
        yield from sec["checks"]


def mapping_problems(crime_id: str = DEFAULT_CRIME) -> list[str]:
    """scoring.json 의 모든 신호·조합 규칙이 정확히 한 검사 항목에 들어 있는지 검사한다. (테스트용)
    fact 신호는 화면에 나오지 않는 조합 조건이라 검사 항목에 넣지 않는다."""
    cfg = config.scoring()
    known = {k for k, v in cfg["signals"].items() if v["tier"] != "fact"} | {c["id"] for c in cfg["combos"]}
    seen: dict[str, str] = {}
    problems = []
    for p in crime_type(crime_id)["principles"]:
        for chk in _checks(p):
            for sid in chk.get("signals", []):
                if sid not in known:
                    problems.append(f"검사 항목 '{chk['id']}'의 신호 '{sid}'가 scoring.json 에 없음")
                if sid in seen:
                    problems.append(f"신호 '{sid}'가 '{seen[sid]}'와 '{chk['id']}' 두 곳에 있음")
                seen[sid] = chk["id"]
    problems += [f"신호 '{sid}'가 어느 검사 항목에도 없음" for sid in sorted(known - set(seen))]
    return problems


def _check_of(p: dict, item_id: str, key: str) -> dict | None:
    return next((c for c in _checks(p) if item_id in c.get(key, [])), None)


def _principle_of(ct: dict, item_id: str, key: str) -> dict | None:
    return next((p for p in ct["principles"] if any(item_id in c.get(key, []) for c in _checks(p))), None)


def _signal_item(s: dict) -> dict:
    return {"type": "signal", "id": s["id"], "tier": s["tier"], "points": s["points"], "label": s["label"],
            "detail": s["detail"], "source": s["source"]}


def _notice_item(n: dict) -> dict:
    return {"type": "notice", "id": n["id"], "severe": bool(n.get("escalate")), "detail": n["detail"],
            "source": "DB" if n.get("escalate") else "규칙"}


def _item_status(i: dict) -> str:
    if i["type"] == "notice":
        return "danger" if i["severe"] else "caution"
    if i["tier"] == "safe":
        return "safe"
    return "danger" if i["tier"] in ("confirmed", "strong") else "caution"


def _check_result(p: dict, chk: dict, signals: dict, notices: dict, unavailable: dict, failed: set,
                  blank: set) -> dict:
    items = [_signal_item(signals[s]) for s in chk.get("signals", []) if s in signals]
    for nid in chk.get("notices", []):
        if nid not in notices:
            continue
        same = next((i for i in items if i["id"] == nid), None)
        if same:  # 같은 뜻의 신호와 주의사항(예: http)은 하나로 합치고, 더 자세한 주의사항 문구를 쓴다
            same["detail"] = notices[nid]["detail"]
        else:
            items.append(_notice_item(notices[nid]))

    if p["kind"] == "placeholder":
        status, summary = "phase2", p.get("phase2_note", "2단계 제공 예정")
    elif chk.get("requires") in blank:
        status, summary = "blank", "단순 QR(안내문 없이 QR만 찍은 사진)이라 검사하지 않음"
    elif items:
        status = max((_item_status(i) for i in items), key=RANK.get)
        summary = items[0]["detail"]
    elif chk.get("requires") and unavailable.get(chk["requires"]):
        status = "unknown" if chk["requires"] in failed else "na"
        summary = unavailable[chk["requires"]]
    else:
        status, summary = ("na" if p["kind"] == "safe" else "safe"), chk["ok"]
    return {"id": chk["id"], "name": chk["name"], "status": status, "summary": summary, "items": items}


def build(*, verdict: str, phishing_verdict: str, risk_reasons: list[dict], safe_reasons: list[dict],
          notices: list[dict], unavailable: dict | None = None, failed: set | None = None,
          blank: set | None = None, crime_id: str = DEFAULT_CRIME) -> dict:
    """판정 결과를 위계에 맞춰 정리한다.

    risk_reasons 는 영향이 큰 순서(scoring.score 결과)여야 한다.
    unavailable: 검사 조건 이름 → 그 검사를 못 한 이유 (예: {"ai": "AI 분석 꺼짐"}). 없는 조건은 검사한 것으로 본다.
    failed: unavailable 중 '하려 했지만 실패한' 조건 이름 (접속 실패·AI 한도 등) → '확인 불가'. 나머지는 '해당 없음'.
    """
    ct = crime_type(crime_id)
    unavailable = unavailable or {}
    failed = failed or set()
    blank = blank or set()  # 결과를 비워 둘('-') 검사 조건 이름 (단순 QR의 안내문 관련 검사)
    sig_by_id = {s["id"]: s for s in risk_reasons + safe_reasons}
    notice_by_id = {n["id"]: n for n in notices}

    report = []
    for p in ct["principles"]:
        sections = [{"name": sec["name"],
                     "checks": [_check_result(p, c, sig_by_id, notice_by_id, unavailable, failed, blank) for c in sec["checks"]]}
                    for sec in p["sections"]]
        checks = [c for sec in sections for c in sec["checks"]]
        issues = sum(c["status"] in ("danger", "caution") for c in checks)
        if p["kind"] == "placeholder":
            status = "phase2"
        elif p["kind"] == "safe":
            status = "info"
        else:
            status = max((c["status"] for c in checks), key=RANK.get, default="na")
        report.append({"id": p["id"], "kind": p["kind"], "short": p.get("short", p["title"]), "title": p["title"],
                       "icon": p.get("icon"), "status": status, "issues": issues, "sections": sections})

    # 대표 수칙: 악성코드 DB > 피싱 근거 중 영향이 가장 큰 것 > (안전 판정이면) 안전 안내가 있는 주의사항
    top, top_item = None, None
    severe = next((n for n in notices if n.get("escalate")), None)
    if severe:
        top, top_item = _principle_of(ct, severe["id"], "notices"), (severe["id"], "notices")
    elif phishing_verdict != "safe" and risk_reasons:
        top, top_item = _principle_of(ct, risk_reasons[0]["id"], "signals"), (risk_reasons[0]["id"], "signals")
    else:
        # 안전 판정이어도 안전 안내(actions.safe)가 있는 수칙의 근거가 있으면 그 안내를 보여 준다.
        # 예: 신뢰 사이트의 실행 파일 다운로드 → 점수는 안전이지만 '공식 마켓에서만 설치' 안내
        found = [(n["id"], "notices") for n in notices] + [(r["id"], "signals") for r in risk_reasons]
        for item_id, key in found:
            p = _principle_of(ct, item_id, key)
            chk = _check_of(p, item_id, key) if p else None
            if p and verdict in {**p.get("actions", {}), **(chk or {}).get("actions", {})}:
                top, top_item = p, (item_id, key)
                break

    # 대표 근거가 들어 있는 검사 항목에 문구(one_liner·actions)가 있으면 수칙 문구 대신 쓴다.
    chk = _check_of(top, *top_item) if top else None
    one_liner = (chk or {}).get("one_liner") or (top or {}).get("one_liner")
    action = ((chk or {}).get("actions", {}).get(verdict) or (top or {}).get("actions", {}).get(verdict)
              or ct["default_actions"][verdict])
    return {
        "crime_type": {"id": ct["id"], "name": ct["name"]},
        "top_rule": {"id": top["id"], "title": top["title"], "short": top.get("short"),
                     "one_liner": one_liner} if top else None,
        "one_liner": one_liner if top else ct["safe_one_liner"],
        "action": action,
        "report": report,
    }
