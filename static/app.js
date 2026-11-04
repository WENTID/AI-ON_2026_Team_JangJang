// QR 피싱 분석기 화면 동작
const $ = (id) => document.getElementById(id);
const views = ["input-view", "loading-view", "result-view"];
const show = (id) => views.forEach((v) => ($(v).hidden = v !== id));
const setMsg = (text) => ($("input-msg").textContent = text || "");

const ICONS = {
  safe: '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.6 3 8.4 7 9.5 4-1.1 7-4.9 7-9.5V6l-7-3Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="m8.5 12 2.5 2.5 4.5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  caution: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4L12 4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4.5M12 17.2v.1" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  danger: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="m9 9 6 6m0-6-6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 분석 요청 ----------
async function analyze(url, imageId = null) {
  stopCamera();
  setMsg("");
  show("loading-view");
  window.scrollTo({ top: 0 });
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, image_id: imageId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "분석에 실패했습니다.");
    renderResult(data);
    show("result-view");
  } catch (e) {
    show("input-view");
    setMsg(e.message);
  }
}

async function uploadImage(blob, decode) {
  const fd = new FormData();
  fd.append("file", blob, "image.jpg");
  fd.append("decode", decode ? "true" : "false");
  const res = await fetch("/api/image", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "이미지를 처리하지 못했습니다.");
  return data;
}

// ---------- URL 직접 입력 ----------
$("url-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = $("url-input").value.trim();
  if (!url) return setMsg("검사할 링크를 입력하세요.");
  analyze(url);
});

// ---------- 이미지 업로드 / 드래그 앤 드롭 ----------
const drop = $("drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && handleFile(e.dataTransfer.files[0]));
$("file").addEventListener("change", (e) => e.target.files[0] && handleFile(e.target.files[0]));

async function handleFile(file) {
  setMsg("");
  $("code-choices").hidden = true;
  if (!file.type.startsWith("image/")) return setMsg("이미지 파일만 올릴 수 있습니다.");
  try {
    const { image_id, codes } = await uploadImage(file, true);
    if (codes.length === 0) {
      setMsg("이미지에서 QR코드를 찾지 못했습니다. QR이 선명하게 보이는 사진을 올려 주세요.");
    } else if (codes.length === 1) {
      pickCode(codes[0], image_id);
    } else {
      $("code-list").innerHTML = "";
      codes.forEach((c) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.textContent = c.text;
        btn.className = "mono";
        btn.onclick = () => pickCode(c, image_id);
        li.appendChild(btn);
        $("code-list").appendChild(li);
      });
      $("code-choices").hidden = false;
    }
  } catch (e) {
    setMsg(e.message);
  } finally {
    $("file").value = "";
  }
}

function pickCode(code, imageId) {
  if (!code.is_url) return setMsg(`QR 내용이 웹 주소가 아닙니다: ${code.text}`);
  $("code-choices").hidden = true;
  analyze(code.text, imageId);
}

// ---------- 카메라 스캔 ----------
let stream = null;
let scanning = false;

$("camera-start").addEventListener("click", () => startCamera());

function setCameraUI(on) {
  $("camera-idle").hidden = on;
  $("camera-hint").hidden = !on;
  $("video").hidden = !on;
}

// auto: 첫 화면에서 자동으로 켜는 경우. 실패하면 조용히 '카메라 켜기' 버튼만 보여 준다.
async function startCamera({ auto = false } = {}) {
  if (stream) return;
  setMsg("");
  if (!navigator.mediaDevices?.getUserMedia) {
    return setMsg("이 주소에서는 카메라를 쓸 수 없습니다. (https 또는 localhost 주소에서만 동작) 아래 '다른 방법으로 검사'를 이용하세요.");
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1280 } } });
  } catch (e) {
    stream = null;
    if (!auto) setMsg("카메라 권한이 없거나 카메라를 찾을 수 없습니다. 아래 '다른 방법으로 검사'를 이용하세요.");
    return;
  }
  const video = $("video");
  video.srcObject = stream;
  await video.play();
  setCameraUI(true);
  scanning = true;
  requestAnimationFrame(scanLoop);
}

function stopCamera() {
  scanning = false;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  setCameraUI(false);
}

function scanLoop() {
  if (!scanning) return;
  const video = $("video");
  if (video.readyState === video.HAVE_ENOUGH_DATA) {
    const canvas = $("frame");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(video, 0, 0);
    const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const code = window.jsQR && jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
    if (code && code.data) {
      onCameraCode(code.data, canvas);
      return;
    }
  }
  requestAnimationFrame(scanLoop);
}

async function onCameraCode(text, canvas) {
  scanning = false;
  if (!/^https?:\/\//i.test(text.trim())) {
    setMsg(`QR 내용이 웹 주소가 아닙니다: ${text}`);
    scanning = true;
    setTimeout(() => requestAnimationFrame(scanLoop), 1500);
    return;
  }
  // QR을 인식한 순간의 전체 화면(안내문 포함)을 저장해 맥락 분석용으로 함께 보낸다.
  let imageId = null;
  try {
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
    imageId = (await uploadImage(blob, false)).image_id;
  } catch (e) {
    imageId = null; // 프레임 저장에 실패해도 분석은 진행
  }
  analyze(text.trim(), imageId);
}

// ---------- 결과 화면 ----------
function sourceTag(source) {
  const cls = source === "AI 판단" ? "ai" : source === "DB" ? "db" : "";
  return `<span class="src ${cls}">${esc(source)}</span>`;
}

// ---------- 점검 결과 트리: 대분류(예방 수칙) > 중분류 > 검사 항목 ----------
const STATUS_LABEL = { danger: "위험", caution: "경고", safe: "안전", na: "해당 없음", phase2: "2단계", info: "참고" };
const CAT_ICONS = {
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.8 2.5 15.2 0 18M12 3c-2.5 2.8-2.5 15.2 0 18"/></svg>',
  form: '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>',
  qr: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><path d="M14 14h2v2h-2zM18 18h2v2h-2zM14 18h2M18 14h2"/></svg>',
  shield: '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.6 3 8.4 7 9.5 4-1.1 7-4.9 7-9.5V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></svg>',
};
let lastResult = null;
const openCats = new Set();
let openCheck = null;
const isIssue = (s) => s === "danger" || s === "caution";
const badge = (s) => `<span class="badge ${s}">${STATUS_LABEL[s]}</span>`;

function checkDetail(c) {
  if (!c.items.length) return `<div class="check-detail"><p>${esc(c.summary)}</p></div>`;
  const rows = c.items.map((i) => {
    const pts = i.type === "signal" && i.points
      ? ` · ${i.tier === "safe" ? "−" : "+"}${i.points}점 (${i.tier === "safe" ? "안전 신호" : i.tier === "weak" ? "약한 신호" : i.source === "조합 규칙" ? "조합 규칙" : i.tier === "confirmed" ? "확정 신호" : "강한 신호"})`
      : i.type === "notice" ? " · 점수와 별개인 주의사항" : "";
    return `<li><p>${esc(i.detail)}</p><p class="muted small">출처: ${esc(i.source)}${pts}</p></li>`;
  }).join("");
  return `<div class="check-detail"><ul>${rows}</ul></div>`;
}

function renderReport() {
  const d = lastResult;
  const only = $("only-issues").checked;
  const cnt = { danger: 0, caution: 0, safe: 0, na: 0, phase2: 0 };
  d.report.filter((c) => c.kind !== "safe").forEach((c) => c.sections.forEach((s) => s.checks.forEach((k) => cnt[k.status]++)));
  const total = Object.values(cnt).reduce((a, b) => a + b, 0);
  $("report-sum").textContent = `점검 항목 ${total}개 · 위험 ${cnt.danger} · 경고 ${cnt.caution} · 안전 ${cnt.safe}`;

  let html = "";
  d.report.forEach((c) => {
    if (only && !c.issues) return;
    const open = openCats.has(c.id);
    html += `<div class="cat"><button class="cat-head" data-cat="${c.id}" aria-expanded="${open}">
      <span class="cat-icon">${CAT_ICONS[c.icon] || ""}</span>
      <span class="cat-name">${esc(c.short)}</span>
      ${c.issues ? `<span class="cat-count">${c.issues}건</span>` : ""}
      ${badge(c.status)}<span class="chev">${open ? "▴" : "▾"}</span></button>`;
    if (open) {
      c.sections.forEach((s) => {
        const checks = s.checks.filter((k) => !only || isIssue(k.status));
        if (!checks.length) return;
        if (s.name) html += `<p class="sec-name">${esc(s.name)}</p>`;
        checks.forEach((k) => {
          const key = `${c.id}/${k.id}`;
          html += `<button class="check" data-check="${key}" aria-expanded="${openCheck === key}">
            <span class="check-name">${esc(k.name)}</span>${badge(k.status)}</button>`;
          if (openCheck === key) html += checkDetail(k);
        });
      });
    }
    html += `</div>`;
  });
  $("report").innerHTML = html || `<p class="muted small">문제 항목이 없습니다.</p>`;
  $("report").querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.cat;
    openCats.has(id) ? openCats.delete(id) : openCats.add(id);
    renderReport();
  }));
  $("report").querySelectorAll("[data-check]").forEach((b) => b.addEventListener("click", () => {
    openCheck = openCheck === b.dataset.check ? null : b.dataset.check;
    renderReport();
  }));
}
$("only-issues").addEventListener("change", renderReport);

function renderResult(d) {
  // 첫 화면: 판정(색상) + 위험도(100점 만점) + 대표 예방 수칙 한 줄 + 행동 안내
  $("verdict").className = `verdict ${d.verdict}`;
  $("verdict-icon").innerHTML = ICONS[d.verdict];
  $("verdict-kind").textContent = d.headline.kind;  // 피싱 / 악성코드 구분
  $("verdict-label").textContent = d.verdict_label;
  const rd = d.risk_display;
  $("risk-value").textContent = rd.value;
  $("risk-fill").style.width = `${rd.value}%`;
  $("mark-caution").style.left = `${rd.caution}%`;
  $("mark-danger").style.left = `${rd.danger}%`;
  $("risk-scale").innerHTML = `<span style="width:${rd.caution}%">안전</span><span style="width:${rd.danger - rd.caution}%">경고 ${rd.caution}</span><span>위험 ${rd.danger}</span>`;
  $("rule-title").hidden = !d.top_rule;
  $("rule-title").textContent = d.top_rule ? `예방 수칙 · ${d.top_rule.title}` : "";
  $("one-liner").textContent = d.one_liner;
  $("action").textContent = d.action;
  $("more").open = false;

  // 점검 결과 트리: 문제가 있는 대분류만 처음부터 펼쳐 둔다.
  lastResult = d;
  openCats.clear();
  openCheck = null;
  $("only-issues").checked = false;
  d.report.filter((c) => c.issues).forEach((c) => openCats.add(c.id));
  renderReport();

  $("r-input").textContent = d.input_url;
  $("r-final").textContent = d.final_url;
  $("r-final-row").hidden = d.final_url === d.input_url;
  const c = d.context;
  $("ctx-notice").textContent = c.notice_purpose || "2단계 제공 예정";
  $("ctx-page").textContent = c.page_purpose || (d.ai.result ? d.ai.result.page_purpose : "-");

  // AI 분석
  const ai = d.ai;
  let html = "";
  if (ai.status === "ok" && ai.result) {
    const r = ai.result;
    html = `<div class="kv">
      <div><span class="k">페이지 목적</span><span class="v">${esc(r.page_purpose)}</span></div>
      <div><span class="k">표방 서비스</span><span class="v">${esc(r.claimed_service || "없음")}</span></div>
      <div><span class="k">요구 정보</span><span class="v">${esc(r.requested_info.join(", ") || "없음")}</span></div>
      <div><span class="k">판단</span><span class="v">${sourceTag("AI 판단")}${esc(ai.message)}</span></div>
      <div><span class="k">근거</span><span class="v muted">${esc(r.reason)}</span></div></div>`;
  } else if (ai.status === "error") {
    html = `<p>AI 분석 불가 <span class="muted small">(${esc(ai.message)}) · 규칙 기반 결과로 판정했습니다.</span></p>`;
  } else {
    html = `<p class="muted">${esc(ai.message)}</p>`;
  }
  if (d.screenshot) html += `<img class="thumb" src="${esc(d.screenshot)}" alt="접속 페이지 미리보기">`;
  $("ai-body").innerHTML = html;

  // 점수·검사 정보 (점수는 상세보기에서만 표시)
  const b = d.breakdown;
  const ck = d.checks;
  $("detail-body").innerHTML = `<div class="kv">
    <div><span class="k">피싱 점수</span><span class="v">${d.score}점 (경고 ${b.thresholds.caution}점 / 위험 ${b.thresholds.danger}점 이상, 화면 위험도는 100점에서 자름)</span></div>
    <div><span class="k">점수 구성</span><span class="v">강한 신호 +${b.strong}, 약한 신호 +${b.weak} (원점수 ${b.weak_raw}, 상한 ${b.weak_cap}), 안전 신호 −${b.safe}</span></div>
    <div><span class="k">접속 결과</span><span class="v">${esc(ck.fetch)}</span></div>
    <div><span class="k">도메인 생성일</span><span class="v">${esc(ck.domain_created)}</span></div>
    <div><span class="k">인증서</span><span class="v">${esc(ck.cert)}</span></div>
    <div><span class="k">Safe Browsing</span><span class="v">${esc(ck.safe_browsing)}</span></div>
    <div><span class="k">OpenPhish</span><span class="v">${esc(ck.phish_feed)}</span></div>
    <div><span class="k">AI 모델</span><span class="v">${esc(ai.model || "-")}</span></div>
  </div>
  <h3>리다이렉트 경로</h3>
  <ol class="chain mono small">${d.redirects.map((h) => `<li>${esc(h.url)} <span class="muted">(${h.status})</span></li>`).join("")}</ol>`;
}

$("again").addEventListener("click", () => {
  show("input-view");
  setMsg("");
  $("url-input").value = "";
  $("alt").open = false;
  window.scrollTo({ top: 0 });
  startCamera({ auto: true });
});

// 예방 수칙 목록을 설정 파일 문구로 채운다. (실패하면 HTML의 기본 문구 유지)
fetch("/api/rules").then((r) => r.json()).then((d) => {
  $("tips-list").innerHTML = d.principles.map((t) => `<li>${esc(t)}</li>`).join("");
}).catch(() => {});

// 첫 화면에서 바로 카메라를 켠다.
setCameraUI(false);
startCamera({ auto: true });
