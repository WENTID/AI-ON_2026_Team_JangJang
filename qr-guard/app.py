"""QR 피싱 판별 웹 데모 (Streamlit).

실행:
    python mock_site/server.py      # 터미널 1 (테스트 시나리오용 가짜 서버)
    streamlit run app.py            # 터미널 2
"""

import html
import json

import pandas as pd
import streamlit as st

from core.pipeline import VERDICT_LABELS, analyze, load_scenarios

st.set_page_config(page_title="QR 피싱 판별", page_icon="🔍", layout="centered")

# 등급별 색 (색만으로 구분하지 않도록 글자도 함께 표시)
VERDICT_COLORS = {"SAFE": "#1e8e3e", "CAUTION": "#e8710a", "DANGER": "#d93025", "UNKNOWN": "#6b7280"}
VERDICT_ICONS = {"SAFE": "✅", "CAUTION": "⚠️", "DANGER": "⛔", "UNKNOWN": "❔"}
NO_SCENARIO = "(선택 안 함 - 실제 검사)"
DISCLAIMER = "이 결과는 참고용이며, 의심되면 결제나 정보 입력 전에 공식 앱이나 기관 대표번호로 확인하세요."

# 하단 고정 주의 문구
st.markdown(
    f"""<style>
    .fixed-disclaimer {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000;
        background: #fff8e1; color: #5f4b00; border-top: 1px solid #f0d58c;
        padding: 8px 16px; font-size: 14px; text-align: center; }}
    .block-container {{ padding-bottom: 80px; }}
    </style>
    <div class="fixed-disclaimer">⚠ {DISCLAIMER}</div>""",
    unsafe_allow_html=True,
)

scenarios = load_scenarios()
scenario_by_label = {f"{s['id']} · {s['title']}": s for s in scenarios}


def on_scenario_change():
    """시나리오를 고르면 안내 문구와 URL 을 채운다."""
    s = scenario_by_label.get(st.session_state["scenario"])
    if s:
        st.session_state["notice"] = s.get("notice_text", "")
        st.session_state["url_input"] = s["qr_url"]


# ── 1. 입력 영역 ─────────────────────────────────────────────
st.title("🔍 QR 피싱 판별")
st.caption("QR 옆 안내 문구와 실제 연결 페이지를 비교해, 안내된 목적에 비해 과도한 정보를 요구하는지 확인합니다.")

st.selectbox("테스트 시나리오 불러오기 (시연용)", [NO_SCENARIO] + list(scenario_by_label),
             key="scenario", on_change=on_scenario_change)
selected = scenario_by_label.get(st.session_state.get("scenario"))
if selected:
    st.info(f"테스트 모드: 실제 인터넷 대신 가짜 서버(127.0.0.1:5001)에 접속합니다. 정답: {selected['expected_verdict']}")

tab_qr, tab_url = st.tabs(["QR 이미지 업로드", "URL 직접 입력"])
with tab_qr:
    uploaded = st.file_uploader("QR 이미지", type=["png", "jpg", "jpeg", "webp", "bmp"])
    if uploaded:
        st.image(uploaded, width=180)
with tab_url:
    st.text_input("URL", key="url_input", placeholder="https://...")
    st.caption("QR 이미지를 올리면 이미지가 우선 사용됩니다.")

st.text_area("QR 주변 안내 문구 (비워도 됨)", key="notice", height=80,
             placeholder="예: 새솔시 공영주차장 요금 결제")

if st.button("검사하기", type="primary", width="stretch"):
    with st.spinner("주소를 따라가며 페이지를 분석하는 중..."):
        st.session_state["result"] = analyze(
            qr_image=uploaded.getvalue() if uploaded else None,
            url=st.session_state.get("url_input", ""),
            notice_text=st.session_state.get("notice", ""),
            test_mode=selected is not None,
            override=selected.get("override") if selected else None,
        )

result = st.session_state.get("result")
if not result:
    st.stop()

llm = result.get("llm") or {}
verdict = result["verdict"]

# ── 2. 판정 카드 ─────────────────────────────────────────────
st.divider()
color = VERDICT_COLORS[verdict]
evidence_html = "".join(f"<li>{html.escape(e)}</li>" for e in result["evidence"]) or "<li>(근거 없음)</li>"
st.markdown(
    f"""<div style="border:3px solid {color}; border-radius:12px; padding:18px 22px; margin-bottom:12px;">
      <div style="font-size:34px; font-weight:800; color:{color};">
        {VERDICT_ICONS[verdict]} {VERDICT_LABELS[verdict]} <span style="font-size:18px;">({verdict})</span></div>
      <div style="margin-top:6px; color:#555;">검사한 주소: <code>{html.escape(result.get('input_url') or '-')}</code></div>
      <ul style="margin-top:12px; font-size:16px;">{evidence_html}</ul>
      <div style="margin-top:8px; padding:10px 12px; background:{color}15; border-radius:8px;">
        <b>권장 행동:</b> {html.escape(result.get('advice') or '-')}</div>
    </div>""",
    unsafe_allow_html=True,
)
for note in result.get("postprocess_notes", []):
    st.caption(f"후처리: {note}")
if result.get("trace") and result["trace"].get("error") and "가짜 서버" in result["trace"]["error"]:
    st.warning("가짜 서버가 꺼져 있는 것 같습니다. 다른 터미널에서 `python mock_site/server.py` 를 실행하세요.")

# ── 3. 목적 비교표 ───────────────────────────────────────────
st.subheader("목적 비교")
excessive = llm.get("excessive_requests") or []


def bullet(items, highlight=()):
    if not items:
        return "-"
    out = []
    for it in items:
        hit = any(it in h or h in it for h in highlight)
        out.append(f"- :red[**{it}** (과도)]" if hit else f"- {it}")
    return "\n".join(out)


col1, col2 = st.columns(2)
with col1:
    st.markdown("**안내된 목적**")
    st.write(llm.get("notice_purpose") or "-")
    st.markdown("**보통 필요한 정보**")
    st.markdown(bullet(llm.get("expected_info")))
with col2:
    st.markdown("**실제 페이지 목적**")
    st.write(llm.get("page_purpose") or "-")
    st.markdown("**실제 요구 정보**")
    st.markdown(bullet(llm.get("requested_info"), excessive))
if excessive:
    st.error("목적에 비해 과도한 요구: " + ", ".join(excessive))
if llm.get("mismatches"):
    st.warning("발견한 불일치: " + " / ".join(llm["mismatches"]))
if verdict == "UNKNOWN" and llm.get("missing_info"):
    st.info("판단에 더 필요한 정보: " + ", ".join(llm["missing_info"]))

# ── 4. URL만 검사했을 때 ─────────────────────────────────────
base = result.get("baseline")
if base:
    st.subheader("URL만 검사했을 때")
    bv = base["verdict"]
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in base["reasons"])
    differ = ("<div style='margin-top:6px; font-weight:700;'>⚡ 판정이 다릅니다</div>"
              if bv != verdict else "")
    st.markdown(
        f"""<div style="border:1px solid #ddd; border-left:6px solid {VERDICT_COLORS[bv]};
             border-radius:8px; padding:10px 14px; font-size:14px;">
          <b style="color:{VERDICT_COLORS[bv]};">{VERDICT_LABELS[bv]} ({bv})</b> · 점수 {base.get('score', 0)}
          <ul style="margin:6px 0 0 0;">{reasons}</ul>{differ}</div>""",
        unsafe_allow_html=True,
    )

# ── 5. 상세 정보 ─────────────────────────────────────────────
with st.expander("상세 정보 (리다이렉트 경로, URL 특징, 입력란, LLM 원본)"):
    tr = result.get("trace") or {}
    st.markdown("**리다이렉트 경로**")
    if tr.get("chain"):
        st.dataframe(pd.DataFrame(tr["chain"]), width="stretch", hide_index=True)
        st.caption(f"최종 URL: {tr.get('final_url')} · 상태 {tr.get('final_status')} · "
                   f"{tr.get('content_type')} · 다운로드: {tr.get('is_download')}")
    if tr.get("error"):
        st.caption(f"오류: {tr['error']}")

    feats = result.get("features") or {}
    if feats.get("initial"):
        st.markdown("**URL 특징**")
        st.dataframe(pd.DataFrame({"최초 URL": feats["initial"], "최종 URL": feats["final"]}).astype(str),
                     width="stretch")

    page = result.get("page")
    if page:
        st.markdown(f"**추출된 입력란** (페이지 제목: {page.get('title') or '-'})")
        if page["inputs"]:
            st.dataframe(pd.DataFrame(page["inputs"]), width="stretch", hide_index=True)
        st.write("요구 정보 범주:", ", ".join(page["requested_info"]) or "-")
        st.write("앱 설치 유도:", ", ".join(page["app_install_links"]) or "-")
        st.write("압박 문구:", ", ".join(page["urgency_phrases"]) or "-")
        if page["forms"]:
            st.dataframe(pd.DataFrame(page["forms"]), width="stretch", hide_index=True)

    st.markdown("**LLM 원본 JSON**")
    st.code(json.dumps(llm, ensure_ascii=False, indent=2), language="json")
