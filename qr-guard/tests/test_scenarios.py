"""가짜 서버로 시나리오 전체를 돌리는 테스트 (LLM 은 부르지 않음).

가짜 서버가 꺼져 있으면 자동으로 건너뛴다.
먼저 실행:  python mock_site/server.py
"""

import httpx
import pytest

from core.pipeline import analyze, load_scenarios

SCENARIOS = load_scenarios()


def _server_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:5001/health", timeout=1).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="가짜 서버(127.0.0.1:5001)가 꺼져 있음")


def _run(sid: str) -> dict:
    s = next(x for x in SCENARIOS if x["id"] == sid)
    return analyze(url=s["qr_url"], notice_text=s["notice_text"], test_mode=True,
                   override=s["override"], use_llm=False)


@pytest.mark.parametrize("s", SCENARIOS, ids=lambda s: s["id"])
def test_baseline_matches_expected(s):
    """3단계 완료 기준: baseline 판정이 정답셋의 baseline_expected 와 일치."""
    r = _run(s["id"])
    assert r["trace"]["error"] is None or r["trace"]["is_download"], r["trace"]
    assert r["baseline"]["verdict"] == s["baseline_expected"], r["baseline"]


def test_redirect_chain_n2():
    r = _run("N2")
    assert [c["url"] for c in r["trace"]["chain"]] == ["https://han.gl/ab12", "https://order.minttable.xyz/t/7"]
    assert r["trace"]["final_url"] == "https://order.minttable.xyz/t/7"


def test_apk_p2():
    r = _run("P2")
    assert r["trace"]["is_download"] is True
    assert r["trace"]["final_url"] == "https://delivery-check.top/install"
    # APK 로 끝나면 LLM 결과와 무관하게 최소 CAUTION
    assert r["verdict"] in ("CAUTION", "DANGER")


def test_page_extract_p1():
    """4단계 완료 기준."""
    info = set(_run("P1")["page"]["requested_info"])
    assert {"card_number", "card_cvc", "card_password", "resident_id"} <= info


def test_page_extract_n1():
    info = set(_run("N1")["page"]["requested_info"])
    assert {"login", "vehicle"} <= info


def test_test_mode_blocks_unknown_url():
    r = analyze(url="https://example.com/", test_mode=True, override={}, use_llm=False)
    assert r["verdict"] == "UNKNOWN"
    assert "override" in r["trace"]["error"]
