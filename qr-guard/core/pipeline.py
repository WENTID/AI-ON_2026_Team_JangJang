"""전체 처리 흐름을 연결하는 함수.

화면(app.py)과 평가 스크립트(eval/run_eval.py)는 모두 analyze() 하나만 호출한다.

커맨드라인 사용 예 (가짜 서버를 먼저 켜야 함):
    python core/pipeline.py P1
    python core/pipeline.py P1 --no-llm      # API 키 없이 LLM 단계만 건너뛰기
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # "python core/pipeline.py" 로 직접 실행할 때를 위해
    sys.path.insert(0, str(ROOT))

from core.analyzer import judge_with_llm, unknown_result
from core.baseline import judge_url_only
from core.page_extract import extract_page
from core.qr_decode import decode_qr
from core.url_features import extract_features, load_brands
from core.url_trace import trace

SCENARIOS_PATH = ROOT / "data" / "scenarios.json"

VERDICT_LABELS = {"SAFE": "안전", "CAUTION": "주의", "DANGER": "위험", "UNKNOWN": "판단 보류"}
# 후처리에서 "최소 CAUTION" 을 적용할 때 쓰는 순서 (UNKNOWN 은 CAUTION 보다 약한 것으로 본다)
SEVERITY = {"UNKNOWN": 0, "SAFE": 0, "CAUTION": 1, "DANGER": 2}


def load_scenarios(path: Path | str = SCENARIOS_PATH) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _url_info_for_llm(input_url, tr, f_initial, f_final) -> dict:
    """LLM 에 넘길 URL 정보 (필요한 것만 간추림)."""
    keys = ["host", "registered_domain", "tld", "is_https", "is_ip", "has_punycode",
            "subdomain_depth", "has_at", "is_shortener", "is_cheap_tld", "lookalike_of"]
    return {
        "qr_url": input_url,
        "redirect_chain": [c["url"] for c in tr["chain"]],
        "final_url": tr["final_url"],
        "final_status": tr["final_status"],
        "is_apk_download": tr["is_download"],
        "initial_url_features": {k: f_initial.get(k) for k in keys},
        "final_url_features": {k: f_final.get(k) for k in keys},
    }


def _page_info_for_llm(page: dict, analyzed_url: str) -> dict:
    """LLM 에 넘길 페이지 정보."""
    return {
        "analyzed_url": analyzed_url,
        "title": page["title"],
        "text_summary": page["text_summary"],
        "forms": page["forms"],
        "inputs": [
            {k: i[k] for k in ("type", "name", "placeholder", "label", "category")}
            for i in page["inputs"] if i.get("category")
        ],
        "requested_info": page["requested_info"],
        "app_install_links": page["app_install_links"],
        "urgency_phrases": page["urgency_phrases"],
    }


def analyze(qr_image=None, url=None, notice_text="", test_mode=False,
            override=None, use_llm=True, use_cache=None) -> dict:
    """QR 이미지 또는 URL 과 안내 문구를 받아 판정 결과 dict 를 돌려준다.

    qr_image  : QR 이미지 바이트 (있으면 url 보다 우선)
    url       : 직접 입력한 URL
    notice_text: QR 주변 안내 문구 (비어도 됨)
    test_mode : True 면 override 로 가짜 서버에만 접속
    override  : {"표시용 URL": "로컬 URL"}
    use_llm   : False 면 LLM 을 부르지 않음 (1~4단계 확인용)
    """
    out = {
        "input_url": None,
        "notice_text": notice_text or "",
        "test_mode": test_mode,
        "verdict": "UNKNOWN",
        "verdict_label": VERDICT_LABELS["UNKNOWN"],
        "evidence": [],
        "advice": "",
        "postprocess_notes": [],
        "llm": None,
        "baseline": None,
        "qr": None,
        "trace": None,
        "features": {"initial": None, "final": None},
        "page": None,
        "error": None,
    }
    try:
        # 1) 입력
        if qr_image:
            qr = decode_qr(qr_image)
            out["qr"] = qr
            if not qr["url"]:
                out["error"] = qr["error"]
                llm = unknown_result(qr["error"] or "QR 에서 URL 을 얻지 못했습니다.",
                                     ["QR 이미지를 더 선명하게 찍거나 URL 을 직접 입력하세요."])
                return _finish(out, llm)
            url = qr["url"]
        url = (url or "").strip()
        out["input_url"] = url
        if not url:
            out["error"] = "QR 이미지나 URL 을 입력하세요."
            return _finish(out, unknown_result(out["error"]))

        # 2) 리다이렉트 추적
        tr = trace(url, test_mode=test_mode, override=override)
        out["trace"] = {k: v for k, v in tr.items() if k not in ("html", "last_html")}

        # 3) URL 특징
        brands = load_brands()
        f_initial = extract_features(url, brands)
        f_final = extract_features(tr["final_url"] or url, brands)
        f_final["is_download"] = tr["is_download"]
        out["features"] = {"initial": f_initial, "final": f_final}

        # 4) baseline
        out["baseline"] = judge_url_only([f_initial, f_final])

        # 5) 페이지 추출 (APK 로 끝났으면 그 직전 페이지를 분석)
        html = tr["html"] or (tr["last_html"] if tr["is_download"] else None)
        analyzed_url = tr["final_url"] if tr["html"] else tr["last_html_url"]
        page = extract_page(html, analyzed_url or url) if html else None
        out["page"] = page

        # 6) LLM 판정 + 7) 후처리
        page_loaded = page is not None and not page.get("error") and (
            tr["final_status"] is None or tr["final_status"] < 400 or tr["is_download"])
        if tr["error"] and not tr["is_download"]:
            llm = unknown_result(f"페이지를 불러오지 못했습니다: {tr['error']}",
                                 ["페이지에 접속할 수 있어야 판단할 수 있습니다."])
            out["postprocess_notes"].append("페이지 접속 실패 → UNKNOWN")
        elif not page_loaded and not tr["is_download"]:
            llm = unknown_result(f"페이지 내용을 확인하지 못했습니다 (상태 코드 {tr['final_status']}).",
                                 ["페이지 내용"])
            out["postprocess_notes"].append("페이지 내용 없음 → UNKNOWN")
        elif not use_llm:
            llm = unknown_result("LLM 판정을 건너뛰었습니다 (--no-llm).")
            out["postprocess_notes"].append("LLM 미사용")
        else:
            page_info = _page_info_for_llm(page, analyzed_url) if page else {"note": "페이지 없이 바로 APK 다운로드"}
            llm = judge_with_llm(notice_text, _url_info_for_llm(url, tr, f_initial, f_final),
                                 page_info, use_cache=use_cache)
            if llm.get("error"):
                out["postprocess_notes"].append(f"LLM 오류 → UNKNOWN ({llm['error']})")

        # APK 다운로드로 이어지면 LLM 결과와 관계없이 최소 CAUTION
        if tr["is_download"] and SEVERITY[llm["verdict"]] < SEVERITY["CAUTION"]:
            before = llm["verdict"]
            llm = dict(llm)
            llm["verdict"] = "CAUTION"
            llm["evidence"] = ["앱 설치 파일(APK) 다운로드로 이어집니다. 출처를 알 수 없는 앱 설치는 위험합니다."] \
                + list(llm.get("evidence") or [])
            llm["advice"] = "앱을 설치하지 말고, 배송·결제 내용은 공식 앱이나 대표번호로 직접 확인하세요."
            out["postprocess_notes"].append(f"APK 다운로드 → {before} 를 CAUTION 으로 상향")

        return _finish(out, llm)

    except Exception as e:  # 어떤 경우에도 화면·평가가 멈추지 않게
        out["error"] = f"분석 중 오류: {e}"
        return _finish(out, unknown_result(out["error"]))


def _finish(out: dict, llm: dict) -> dict:
    out["llm"] = llm
    out["verdict"] = llm["verdict"]
    out["verdict_label"] = VERDICT_LABELS[llm["verdict"]]
    out["evidence"] = llm.get("evidence") or []
    out["advice"] = llm.get("advice") or ""
    return out


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="시나리오 ID 로 판정을 실행합니다.")
    parser.add_argument("scenario_id", help="예: P1, N2, A1")
    parser.add_argument("--no-llm", action="store_true", help="LLM 판정을 건너뜀")
    parser.add_argument("--full", action="store_true", help="결과 전체를 출력")
    args = parser.parse_args(argv)

    scenarios = {s["id"].upper(): s for s in load_scenarios()}
    s = scenarios.get(args.scenario_id.upper())
    if not s:
        print(f"시나리오 '{args.scenario_id}' 가 없습니다. 가능한 ID: {', '.join(scenarios)}")
        return 1

    result = analyze(url=s["qr_url"], notice_text=s.get("notice_text", ""),
                     test_mode=True, override=s.get("override"), use_llm=not args.no_llm)

    if result["trace"] and result["trace"].get("error") and "연결할 수 없습니다" in result["trace"]["error"]:
        print("⚠ 가짜 서버에 연결할 수 없습니다. 다른 터미널에서 먼저 실행하세요:  python mock_site/server.py")

    if args.full:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = {
            "scenario": s["id"],
            "expected": s.get("expected_verdict"),
            "verdict": result["verdict"],
            "baseline": result["baseline"]["verdict"] if result["baseline"] else None,
            "postprocess_notes": result["postprocess_notes"],
            "llm": result["llm"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # 윈도우 콘솔에서 한글이 깨지지 않게
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(_main(sys.argv[1:]))
