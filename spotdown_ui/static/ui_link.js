"use strict";
// The Spotify link flow (ADR 0005): link -> tracks -> pick one -> YouTube match -> analysis and
// the mp3. Every server string is rendered through escapeHtml; ids go into paths encoded.
const tracksCard = $("tracksCard"), matchCard = $("matchCard"), matchInfo = $("matchInfo");
const downloadEl = $("download");
let tracks = [], picked = null, match = null, audioId = null;

function trackLabel(t) {
  const artists = Array.isArray(t.artists) ? t.artists.join(", ") : "";
  return `${escapeHtml(t.title || "")}${artists ? " — " + escapeHtml(artists) : ""}`;
}

function renderTracks(d) {
  tracks = Array.isArray(d.tracks) ? d.tracks : [];
  tracksCard.style.display = "block";
  if (!tracks.length) { tracksCard.innerHTML = `<div class="hint">No tracks found.</div>`; return; }
  const items = tracks.map((t, i) =>
    `<div class="mrow"><button class="ghost pick" data-i="${i}">Pick</button>` +
    `<span class="mv">${trackLabel(t)} <span class="dim">${escapeHtml(fmtDur(Math.round((t.duration_ms || 0) / 1000)) || "")}</span></span></div>`
  ).join("");
  const more = d.truncated ? `<div class="hint">Only the first ${tracks.length} tracks are shown.</div>` : "";
  tracksCard.innerHTML = `<div style="font-weight:600">Tracks</div>${items}${more}`;
  for (const b of tracksCard.querySelectorAll("button.pick")) {
    b.addEventListener("click", () => pickTrack(tracks[+b.getAttribute("data-i")]));
  }
}

async function lookup() {
  const url = $("url").value.trim();
  if (!url) return;
  showStatus('<span class="spin"></span>Looking up…');
  try {
    renderTracks(await apiJson("/api/spotify/resolve", { method: "POST", json: { url } }));
    showStatus("Pick a track.");
  } catch (e) { showStatus("Lookup failed: " + e.message, true); }
}

function videoId(source) {
  return typeof source === "string" && /^youtube:[A-Za-z0-9_-]{11}$/.test(source) ? source.slice(8) : null;
}

function renderMatch(t, r) {
  matchCard.style.display = "block";
  downloadEl.innerHTML = "";
  const best = r && r.best, vid = best && videoId(best.source);
  if (!vid || r.status !== "matched" && r.status !== "uncertain") {
    match = null;
    matchInfo.innerHTML = `<div>${trackLabel(t)}</div><div class="hint err">No YouTube match: ${escapeHtml((r && r.status) || "unknown")}.</div>`;
    return;
  }
  match = { source_ref: best.source, track: t };
  const link = `https://www.youtube.com/watch?v=${vid}`;
  matchInfo.innerHTML = `<div style="font-weight:600">YouTube match</div><div>${trackLabel(t)}</div>
    <div class="hint">Status ${escapeHtml(r.status)} · confidence ${escapeHtml(best.recording_match_confidence)} ·
    duration check ${escapeHtml(best.duration_check)} · <a class="dl" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(vid)} ↗</a></div>`;
}

async function pickTrack(t) {
  if (!t) return;
  picked = t; match = null; audioId = null;
  showStatus('<span class="spin"></span>Matching on YouTube…');
  try {
    const track = { title: t.title, artists: t.artists || [], duration_s: (t.duration_ms || 0) / 1000 };
    if (t.isrc) track.isrc = t.isrc;
    const d = await apiJson("/api/match", { method: "POST", json: { tracks: [track] } });
    renderMatch(t, d.results && d.results[0]);
    showStatus(match ? "Match found." : "No usable match.", !match);
  } catch (e) { showStatus("Match failed: " + e.message, true); }
}

// The waveform's audio for a match: the mp3 this page already asked for, else none.
async function loadMatchAudio() {
  if (!audioId) return null;
  const r = await api(`/api/audio/${encodeURIComponent(audioId)}`);
  if (!r.ok) throw new Error("the mp3 download failed (" + (await problem(r)) + ")");
  return r.arrayBuffer();
}

async function analyzeMatch() {
  if (!match) return showStatus("Pick a track with a match first.", true);
  $("analyze").disabled = true;
  showStatus('<span class="spin"></span>Starting the analysis…');
  try {
    const body = { source_ref: match.source_ref };
    const sid = match.track.spotify_id, ms = match.track.duration_ms;
    if (typeof sid === "string" && typeof ms === "number" && ms > 0) {
      body.reference = { kind: "spotify", id: sid, duration_s: ms / 1000 };
    }
    const job = await waitForJob(await apiJson("/api/analyses", { method: "POST", json: body }), "Analysing on the server…");
    await showAnalysis(job, loadMatchAudio);
  } catch (e) { showStatus("Analysis failed: " + e.message, true); }
  finally { $("analyze").disabled = false; }
}

async function makeMp3() {
  if (!match) return showStatus("Pick a track with a match first.", true);
  $("mp3").disabled = true;
  showStatus('<span class="spin"></span>Asking for the mp3…');
  try {
    const t = match.track;
    const body = { source_ref: match.source_ref, bitrate: parseInt($("bitrate").value, 10) };
    if (t.title) body.title = t.title;
    if (Array.isArray(t.artists) && t.artists.length) body.artists = t.artists;
    const job = await waitForJob(await apiJson("/api/audio", { method: "POST", json: body }), "Making the mp3…");
    if (job.status !== "done" || typeof job.audio_id !== "string") throw new Error(jobProblem(job));
    audioId = job.audio_id;
    downloadEl.innerHTML = `<a class="dl" href="/api/audio/${encodeURIComponent(audioId)}" download>⬇ Download the mp3</a>`;
    showStatus("The mp3 is ready.");
  } catch (e) { showStatus("The mp3 failed: " + e.message, true); }
  finally { $("mp3").disabled = false; }
}

$("lookup").addEventListener("click", lookup);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });
$("analyze").addEventListener("click", analyzeMatch);
$("mp3").addEventListener("click", makeMp3);
