"use strict";
// The service-mode page (ADR 0004): a local file analysed through the authenticated v2 API
// (docs/contract-v2.md), never through a standalone route. The service key the user types is kept
// in this one variable only: never in localStorage, sessionStorage, a cookie, the URL or the DOM,
// and never logged. Every request sends it as `Authorization: Bearer`, the header /v2 requires; a
// 401 forgets it. The waveform is drawn from the picked file itself (the server never sends audio).
const UI_SCOPE = "spotdown_ui";  // the cache_scope of every analysis made from this page
const POLL_MS = 1500, POLL_MAX = 800;  // about 20 minutes, past the analysis wall-clock limit
let serviceKey = null;
const keyInput = $("key"), keyState = $("keyState"), fileInput = $("file"), dropZone = $("drop");

const KEY_STATES = {
  required: "Service key required: enter it to analyse.",
  checking: "Checking the service key…",
  accepted: "Service key accepted (kept in this tab's memory only).",
  rejected: "Service key refused: enter it again.",
  unchecked: "Service key set, but the server could not be reached to check it.",
};
function setKeyState(state) {
  keyState.className = "hint" + (state === "rejected" || state === "required" ? " err" : "");
  keyState.textContent = KEY_STATES[state];
}
function forgetKey(state) { serviceKey = null; setKeyState(state || "required"); }

class KeyRequired extends Error {}

// Same-origin requests only (no CORS in service mode), no cookies, no referrer, no cache.
async function v2(path, opts) {
  if (!serviceKey) throw new KeyRequired("service key required");
  const o = opts || {};
  const headers = Object.assign({}, o.headers || {}, { Authorization: "Bearer " + serviceKey });
  if (o.json !== undefined) headers["Content-Type"] = "application/json";
  else if (o.binary !== undefined) headers["Content-Type"] = "application/octet-stream";
  const r = await fetch(path, {
    method: o.method || "GET", headers, credentials: "omit", mode: "same-origin",
    cache: "no-store", referrerPolicy: "no-referrer",
    body: o.json !== undefined ? JSON.stringify(o.json) : o.binary,
  });
  if (r.status === 401) { forgetKey("rejected"); throw new KeyRequired("the service key was refused"); }
  return r;
}

function idempotencyKey() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  return "ui-" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

// The v2 error envelope's message, else the HTTP status (shown as plain text by showStatus).
async function problem(r) {
  try {
    const d = await r.json();
    if (d && d.error && typeof d.error.message === "string") return d.error.message;
  } catch (e) { /* not JSON */ }
  return "HTTP " + r.status;
}

// A 404 for a well-formed job id proves the key without touching any data.
async function checkKey() {
  setKeyState("checking");
  try {
    const r = await v2(`/v2/jobs/job_keycheck?cache_scope=${UI_SCOPE}`);
    setKeyState(r.status === 404 || r.ok ? "accepted" : "unchecked");
  } catch (e) {
    if (!(e instanceof KeyRequired)) setKeyState("unchecked");
  }
}

function useKey() {
  const typed = keyInput.value.trim();
  keyInput.value = "";  // the key leaves the DOM at once
  if (!typed) return forgetKey("required");
  serviceKey = typed;
  return checkKey();
}

// One analysis at a time: the picker and the drop zone are off while one runs.
let busy = false;
function setBusy(on) { busy = on; fileInput.disabled = on; dropZone.className = on ? "drop busy" : "drop"; }

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

async function waitForJob(job) {
  for (let i = 0; i < POLL_MAX; i++) {
    if (job.status === "done" || job.status === "failed") return job;
    showStatus(`<span class="spin"></span>${job.status === "running" ? "Analysing on the server…" : "Queued…"}`);
    await sleep(POLL_MS);
    const r = await v2(`/v2/jobs/${encodeURIComponent(job.job_id)}?cache_scope=${UI_SCOPE}`);
    if (!r.ok) throw new Error(await problem(r));
    job = await r.json();
  }
  throw new Error("timed out waiting for the server");
}

async function analyzeFile(file) {
  if (!file || busy) return;
  if (!serviceKey) { setKeyState("required"); return showStatus("Service key required: enter it above first.", true); }
  setBusy(true);
  showStatus('<span class="spin"></span>Uploading the file…');
  try {
    const up = await v2(`/v2/uploads?cache_scope=${UI_SCOPE}`,
      { method: "PUT", binary: file, headers: { "Idempotency-Key": idempotencyKey() } });
    if (!up.ok) throw new Error(await problem(up));
    const upload = await up.json();
    const post = await v2("/v2/analyses", { method: "POST", headers: { "Idempotency-Key": idempotencyKey() },
      json: { cache_scope: UI_SCOPE, upload_id: upload.upload_id } });
    if (!post.ok) throw new Error(await problem(post));
    const job = await waitForJob(await post.json());
    if (job.status !== "done") throw new Error(`${job.outcome || "failed"} (${job.failure_kind || "no detail"})`);
    const a = await v2(`/v2/analyses/${encodeURIComponent(job.analysis_id)}?cache_scope=${UI_SCOPE}`);
    if (!a.ok) throw new Error(await problem(a));
    renderAnalysis({ document: await a.json() }, () => file.arrayBuffer(), { local: true, via: "service" });
    showStatus("Analysis ready.");
  } catch (e) {
    showStatus((e instanceof KeyRequired ? "Service key required: " : "Analysis failed: ") + e.message, true);
  } finally { setBusy(false); }
}

$("useKey").addEventListener("click", useKey);
$("forgetKey").addEventListener("click", () => { keyInput.value = ""; forgetKey("required"); });
keyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") useKey(); });
fileInput.addEventListener("change", () => analyzeFile(fileInput.files && fileInput.files[0]));
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); if (!busy) dropZone.className = "drop over"; });
dropZone.addEventListener("dragleave", () => { if (!busy) dropZone.className = "drop"; });
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  if (busy) return;
  dropZone.className = "drop";
  analyzeFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});
setKeyState("required");
