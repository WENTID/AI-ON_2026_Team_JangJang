// QR 피싱 분석기 화면 동작
const $ = (id) => document.getElementById(id);
const views = ["input-view", "loading-view", "result-view"];
const show = (id) => views.forEach((v) => ($(v).hidden = v !== id));

const VERDICT_DESC = {
  safe: "피싱 위험 신호가 거의 없습니다.",
  caution: "피싱 가능성이 있습니다. 개인정보·금융정보를 입력하지 마세요.",
  danger: "피싱 사이트일 가능성이 높습니다. 접속하지 마세요.",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 탭 ----------
document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    ["camera", "upload", "url"].forEach((name) => ($(`panel-${name}`).hidden = name !== tab.dataset.tab));
    if (tab.dataset.tab !== "camera") stopCamera();
  })
);

// ---------- 분석 요청 ----------
async function analyze(url, imageId = null) {
  stopCamera();
  show("loading-view");
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
    window.scrollTo(0, 0);
  } catch (e) {
    show("input-view");
    $("url-msg").textContent = e.message;
    $("upload-msg").textContent = e.message;
    $("camera-msg").textContent = e.message;
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
  if (!url) return ($("url-msg").textContent = "URL을 입력하세요.");
  $("url-msg").textContent = "";
  analyze(url);
});

// ---------- 이미지 업로드 / 드래그 앤 드롭 ----------
const drop = $("drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && handleFile(e.dataTransfer.files[0]));
$("file").addEventListener("change", (e) => e.target.files[0] && handleFile(e.target.files[0]));

async function handleFile(file) {
  $("upload-msg").textContent = "";
  $("code-choices").hidden = true;
  if (!file.type.startsWith("image/")) return ($("upload-msg").textContent = "이미지 파일만 올릴 수 있습니다.");
  try {
    const { image_id, codes } = await uploadImage(file, true);
    if (codes.length === 0) {
      $("upload-msg").textContent = "이미지에서 QR코드를 찾지 못했습니다. QR이 선명하게 보이는 사진을 올려 주세요.";
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
    $("upload-msg").textContent = e.message;
  } finally {
    $("file").value = "";
  }
}

function pickCode(code, imageId) {
  if (!code.is_url) {
    $("upload-msg").textContent = `QR 내용이 웹 주소가 아닙니다: ${code.text}`;
    return;
  }
  analyze(code.text, imageId);
}

// ---------- 카메라 스캔 ----------
let stream = null;
let scanning = false;

$("camera-start").addEventListener("click", startCamera);

async function startCamera() {
  $("camera-msg").textContent = "";
  if (!navigator.mediaDevices?.getUserMedia) {
    $("camera-msg").textContent = "이 브라우저에서는 카메라를 쓸 수 없습니다. (https 또는 localhost 에서만 동작)";
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1280 } } });
  } catch (e) {
    $("camera-msg").textContent = "카메라 권한이 없거나 카메라를 찾을 수 없습니다.";
    return;
  }
  const video = $("video");
  video.srcObject = stream;
  await video.play();
  $("camera-start").hidden = true;
  scanning = true;
  requestAnimationFrame(scanLoop);
}

function stopCamera() {
  scanning = false;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  $("camera-start").hidden = false;
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
    $("camera-msg").textContent = `QR 내용이 웹 주소가 아닙니다: ${text}`;
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

function reasonItem(r) {
  return `<li class="${r.tier}">${sourceTag(r.source)}${esc(r.detail)}</li>`;
}

function renderResult(d) {
  const v = $("verdict");
  v.className = `verdict ${d.verdict}`;
  $("verdict-label").textContent = d.verdict_label;
  $("verdict-desc").textContent = VERDICT_DESC[d.verdict];
  const n = d.notices.length;
  $("notice-badge").hidden = n === 0;
  $("notice-badge").textContent = `주의사항 ${n}건`;

  $("r-input").textContent = d.input_url;
  $("r-final").textContent = d.final_url;
  $("r-final-row").hidden = d.final_url === d.input_url;

  // 판정 근거: 핵심 3개는 바로, 나머지는 펼쳐보기
  const risks = d.risk_reasons;
  $("risk-top").innerHTML = risks.slice(0, 3).map(reasonItem).join("");
  $("risk-more").innerHTML = risks.slice(3).map(reasonItem).join("");
  $("risk-more-wrap").hidden = risks.length <= 3;
  $("risk-empty").hidden = risks.length > 0;
  $("safe-title").hidden = d.safe_reasons.length === 0;
  $("safe-list").innerHTML = d.safe_reasons.map((r) => `<li>${sourceTag(r.source)}${esc(r.detail)}</li>`).join("");

  // 기타 주의사항: 없으면 섹션 숨김
  $("notice-card").hidden = n === 0;
  $("notice-list").innerHTML = d.notices.map((x) => `<li>${esc(x.detail)}</li>`).join("");

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

  // 맥락 비교 (2단계)
  const c = d.context;
  $("ctx-notice").textContent = c.notice_purpose || (c.status === "phase2" ? "2단계 제공 예정" : "맥락 정보 없음");
  $("ctx-page").textContent = c.page_purpose || (ai.result ? ai.result.page_purpose : "-");
  $("ctx-msg").textContent = c.message;

  // 상세 정보 (점수는 여기서만 표시)
  const b = d.breakdown;
  const ck = d.checks;
  $("detail-body").innerHTML = `<div class="kv">
    <div><span class="k">피싱 점수</span><span class="v">${d.score}점 (주의 ${b.thresholds.caution}점 / 위험 ${b.thresholds.danger}점 이상)</span></div>
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
  ["url-msg", "upload-msg", "camera-msg"].forEach((id) => ($(id).textContent = ""));
  $("url-input").value = "";
});
