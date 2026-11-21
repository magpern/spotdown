const $ = (id) => document.getElementById(id);
// When opened as a served page, use relative URLs. When opened as a local file
// (e.g. an editor preview), fall back to the dev server so fetch() still works.
const API_BASE = (location.protocol === "http:" || location.protocol === "https:")
  ? "" : "http://localhost:8000";
const trackCard = $("trackCard"), statusCard = $("statusCard"), statusEl = $("status");
const analysisCard = $("analysisCard");
let currentTrackId = null, currentAudioUrl = null;

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
function renderTrack(t) {
  if ((t.id || null) !== currentTrackId) currentAudioUrl = null;  // the audio belongs to one track
  currentTrackId = t.id || null;
  trackCard.style.display = "block";
  const cover = safeUrl(t.cover_url) ? `<img src="${escapeHtml(t.cover_url)}" alt="">` : `<img alt="">`;
  const artist = (t.artists && t.artists.length) ? t.artists.join(", ") : "";
  const rows = [
    ["Title", t.title],
    ["Artists", artist],
    ["Album", t.album],
    ["Duration", fmtDur(t.duration_s)],
    ["ISRC", t.isrc],
    ["Track ID", t.id],
  ];
  const meta = rows.map(([k, v]) =>
    `<div class="mrow"><span class="mk">${k}</span>` +
    `<span class="mv">${v ? escapeHtml(String(v)) : '<span class="dim">—</span>'}</span></div>`
  ).join("");
  const link = safeUrl(t.spotify_url)
    ? `<a class="dl" href="${escapeHtml(t.spotify_url)}" target="_blank" rel="noopener">Open on Spotify ↗</a>` : "";
  const sparse = !artist || !t.album || t.duration_s == null;
  const hint = sparse
    ? `<div class="hint">Only title + cover come from Spotify's public oEmbed endpoint. ` +
      `Set <code>SPOTIFY_CLIENT_ID</code> / <code>SPOTIFY_CLIENT_SECRET</code> on the server ` +
      `for artists, album, duration and ISRC.</div>` : "";
  trackCard.innerHTML = `<div class="track">${cover}
    <div><div class="t">${escapeHtml(t.title || "")}</div>
    <div class="a">${escapeHtml(artist || t.album || "")}</div></div></div>
    <div class="meta">${meta}</div>${link}${hint}`;
}
function escapeHtml(s){return (s === null || s === undefined ? "" : String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// Only http(s) links from the server go into href/src (never javascript: or data:).
function safeUrl(u){return typeof u === "string" && /^https?:\/\//i.test(u);}

async function lookup() {
  const url = $("url").value.trim();
  if (!url) return;
  showStatus('<span class="spin"></span>Looking up…');
  try {
    const r = await fetch(`${API_BASE}/api/Gettrack?spotify_url=${encodeURIComponent(url)}`);
    const d = await r.json();
    if (!r.ok) return showStatus("Lookup failed: " + (d.detail || r.status), true);
    if (!d.tracks || !d.tracks.length) return showStatus("No tracks found.", true);
    renderTrack(d.tracks[0]);
    showStatus(d.count > 1 ? `Resolved ${d.count} tracks (showing first).` : "Metadata loaded.");
  } catch (e) { showStatus("Network error: " + e.message, true); }
}

async function convert() {
  const url = $("url").value.trim();
  if (!url) return showStatus("Enter a Spotify URL first.", true);
  const body = { spotify_url: url, format: $("format").value, bitrate: parseInt($("bitrate").value, 10) };

  $("convert").disabled = true;
  showStatus('<span class="spin"></span>Requesting conversion…');
  try {
    const r = await fetch(`${API_BASE}/api/v1/convert`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    if (d.track) renderTrack(d.track);
    if (d.status === "done") return done(d.download_url);
    await poll(d.task_id);
  } catch (e) {
    showStatus("Convert failed: " + e.message, true);
  } finally { $("convert").disabled = false; }
}

async function poll(taskId) {
  for (let i = 0; i < 120; i++) {
    const r = await fetch(`${API_BASE}/api/v1/tasks/${encodeURIComponent(taskId)}`);
    const d = await r.json();
    if (d.status === "done") return done(d.download_url);
    if (d.status === "failed") return showStatus("Conversion failed:\n" + (d.error || "unknown error"), true);
    showStatus(`<span class="spin"></span>${d.status === "processing" ? "Converting on the server…" : "Queued…"}`);
    await new Promise(res => setTimeout(res, 1500));
  }
  showStatus("Timed out waiting for the server.", true);
}

function done(downloadUrl) {
  if (!safeUrl(downloadUrl) && !(typeof downloadUrl === "string" && /^\/(?!\/)/.test(downloadUrl)))
    return showStatus("The server returned an unusable download link.", true);
  currentAudioUrl = downloadUrl;
  showStatus(`Done. <a class="dl" href="${escapeHtml(downloadUrl)}" download>⬇ Download file</a>`);
}

// The converted file the server already offered for download (no new endpoint); null if this page
// has not converted the track. #49 can return a local file's bytes here instead.
async function loadTrackAudio() {
  if (!currentAudioUrl) return null;
  const r = await fetch(currentAudioUrl);
  if (!r.ok) throw new Error("the audio download failed (HTTP " + r.status + ")");
  return r.arrayBuffer();
}

async function analyze() {
  if (!currentTrackId) return showStatus("Look up a track first.", true);
  $("analyze").disabled = true;
  showStatus('<span class="spin"></span>Analyzing audio… (first run can take ~10s)');
  try {
    const r = await fetch(`${API_BASE}/api/v1/analyze/${encodeURIComponent(currentTrackId)}`);
    const d = await r.json();
    if (r.status === 404) return showStatus("Convert the track first — analysis needs the downloaded file.", true);
    if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    renderAnalysis(d);
    showStatus("Analysis ready.");
  } catch (e) {
    showStatus("Analysis failed: " + e.message, true);
  } finally { $("analyze").disabled = false; }
}

$("lookup").addEventListener("click", lookup);
$("convert").addEventListener("click", convert);
$("analyze").addEventListener("click", analyze);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });
