"use strict";
// The page's one way to the server (ADR 0005): same-origin /api requests with the session cookie
// and X-Spotdown-UI (ADR 0003). The page never sees the backend, its address or its key: the UI
// server makes every backend call itself. A 401 means the session is gone, so sign in again.
async function api(path, opts) {
  const o = opts || {};
  const headers = { "X-Spotdown-UI": "1" };
  if (o.json !== undefined) headers["Content-Type"] = "application/json";
  else if (o.binary !== undefined) headers["Content-Type"] = "application/octet-stream";
  const r = await fetch(path, {
    method: o.method || "GET", headers, credentials: "same-origin", mode: "same-origin",
    cache: "no-store", referrerPolicy: "no-referrer",
    body: o.json !== undefined ? JSON.stringify(o.json) : o.binary,
  });
  if (r.status === 401) { location.assign("/login"); throw new Error("signed out"); }
  return r;
}

// An error answer's code (the UI's own envelope, or the session layer's detail), else the status.
async function problem(r) {
  try {
    const d = await r.json();
    if (d && d.error && typeof d.error.code === "string") return d.error.code;
    if (d && typeof d.detail === "string") return d.detail;
  } catch (e) { /* not JSON */ }
  return "HTTP " + r.status;
}

async function apiJson(path, opts) {
  const r = await api(path, opts);
  if (!r.ok) throw new Error(await problem(r));
  return r.json();
}

const POLL_MS = 1500, POLL_MAX = 800;  // about 20 minutes, past the backend's job limits
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

// Polls a job until it is done or failed; `label` is the status line while it runs.
async function waitForJob(job, label) {
  for (let i = 0; i < POLL_MAX; i++) {
    if (job.status === "done" || job.status === "failed") return job;
    showStatus(`<span class="spin"></span>${job.status === "running" ? escapeHtml(label) : "Queued…"}`);
    await sleep(POLL_MS);
    job = await apiJson(`/api/jobs/${encodeURIComponent(job.job_id)}`);
  }
  throw new Error("timed out waiting for the server");
}

function jobProblem(job) {
  return `${job.outcome || "failed"} (${job.failure_kind || "no detail"})`;
}

async function showAnalysis(job, loadAudio, opts) {
  if (job.status !== "done") throw new Error(jobProblem(job));
  const doc = await apiJson(`/api/analyses/${encodeURIComponent(job.analysis_id)}`);
  renderAnalysis({ document: doc }, loadAudio, Object.assign({ via: "service" }, opts || {}));
  showStatus("Analysis ready.");
}

$("logout").addEventListener("click", async () => {
  try { await api("/logout", { method: "POST", json: {} }); } finally { location.assign("/login"); }
});
