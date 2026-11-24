// Shared by both UI pages (index.html in standalone mode, service.html in service mode; ADR 0004):
// element lookup, the status line, and the escaping every rendered string goes through. No request.
const $ = (id) => document.getElementById(id);
const statusCard = $("statusCard"), statusEl = $("status"), analysisCard = $("analysisCard");

function showStatus(html, isErr) {
  statusCard.style.display = "block";
  statusEl.className = "status" + (isErr ? " err" : "");
  // Errors are plain text (they carry server detail and exception messages); only our own
  // literal markup (spinner, download link) comes in as html.
  statusEl.innerHTML = isErr ? escapeHtml(html) : html;
}
function fmtDur(s) {
  if (s === null || s === undefined) return null;
  const m = Math.floor(s / 60), ss = String(s % 60).padStart(2, "0");
  return `${m}:${ss}`;
}
function escapeHtml(s){return (s === null || s === undefined ? "" : String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// Only http(s) links from the server go into href/src (never javascript: or data:).
function safeUrl(u){return typeof u === "string" && /^https?:\/\//i.test(u);}
