"use strict";
// The "Audio analysis" card: stats, timeline with a window control, and the detail tables.
let tlModel = null, tlWindow = { start: 0, len: 0 };  // len 0 = whole track

const pct = (v) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : "—");
const tm = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const tag = (method) => (method === "heuristic" ? `<span class="tag">heuristic</span>` : "");
const esc = (v) => escapeHtml(v === null || v === undefined ? "" : String(v));

function drawTimeline() {
  const el = $("tlHost");
  if (!el || !tlModel) return;
  const W = Math.max(el.clientWidth, 320), dur = tlModel.duration;
  const len = tlWindow.len || dur, t0 = Math.min(tlWindow.start, Math.max(dur - len, 0));
  el.innerHTML = timelineSvg(tlModel, t0, Math.min(t0 + len, dur), W);
}

function timelineControls(m) {
  const opts = [[0, "whole track"], [20, "20 s"], [40, "40 s"], [80, "80 s"]]
    .map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
  return `<div class="tl-ctl"><span>Window</span><select id="tlLen">${opts}</select>` +
    `<input id="tlStart" type="range" min="0" max="${Math.floor(m.duration)}" value="0" disabled>` +
    `<span id="tlRange"></span></div>`;
}

function wireTimeline() {
  const len = $("tlLen"), start = $("tlStart");
  const sync = () => {
    tlWindow = { start: +start.value, len: +len.value };
    start.disabled = !tlWindow.len;
    $("tlRange").textContent = tlWindow.len ? `${tm(tlWindow.start)}–${tm(Math.min(tlWindow.start + tlWindow.len, tlModel.duration))}` : "";
    drawTimeline();
  };
  len.addEventListener("change", sync);
  start.addEventListener("input", sync);
  tlWindow = { start: 0, len: 0 };
  drawTimeline();
}

function statsHtml(m) {
  const meter = m.meter ? `${m.meter.bpb}/4` : "—";
  const cells = [
    [m.tempo.bpm, "BPM"], [meter, "Meter"], [fmtDur(Math.round(m.duration)), "Length"],
    [m.drops.length, "Drops"], [m.sections.length, "Sections"], [m.phrases.length, "Phrases"],
  ];
  return `<div class="stats">` + cells.map(([v, l]) =>
    `<div class="stat"><div class="sv">${v}</div><div class="sl">${l}</div></div>`).join("") + `</div>`;
}

function tempoHtml(m) {
  const cand = m.tempo.candidates.map((c) => `${c.bpm} (${pct(c.conf)})`).join(", ");
  const meter = m.meter
    ? `${m.meter.bpb}/4 conf ${pct(m.meter.conf)} ${tag(m.meter.method)}`
    : "not claimed (gate not passed)";
  const bars = m.downbeats ? `${m.downbeats.length} bars, downbeat conf ${pct(m.downbeatConf)}` : "no downbeats";
  return `<div class="hint">Tempo ${m.tempo.bpm} BPM, confidence ${pct(m.tempo.conf)}, stability ${pct(m.tempo.stability)}.
    Half/double-time candidates: ${cand}.<br>Meter ${meter} · ${m.beats.length} beats · ${bars}.</div>`;
}

function signalHtml(m) {
  const reg = m.silences.map((r) => `${tm(r.start)}–${tm(r.end)}`).join(", ") || "none";
  const fade = (f, n) => (f ? `${n} ${tm(f.start)}–${tm(f.end)} (conf ${pct(f.conf)})` : `no ${n}`);
  return `<div class="hint">Content ${tm(m.contentStart)}–${tm(m.contentEnd)} of ${tm(m.duration)} ·
    silence: ${reg} · ${fade(m.fadeIn, "fade in")}, ${fade(m.fadeOut, "fade out")}.</div>`;
}

function alignmentHtml(m, local) {
  const a = m.alignment;
  if (!a && local) return `<div class="hint">Alignment: none (a local file has no reference recording).</div>`;
  if (!a) return `<div class="hint">Alignment: none (no Spotify duration known).</div>`;
  if (a.result !== "ok") {
    const h = a.hypotheses.map((x) => `${esc(x.name)} residual ${pct(x.residual)}s`).join("; ");
    return `<div class="hint"><span class="badge warn">alignment failed</span> ${h}. Times are file-clock only.</div>`;
  }
  return `<div class="hint"><span class="badge ok">aligned</span> Spotify time = file time ${a.offset >= 0 ? "+" : "−"} ${Math.abs(a.offset)}s ·
    residual ${a.residual}s · confidence ${pct(a.conf)} · Spotify duration ${a.refDuration}s.</div>`;
}

function sectionsHtml(m) {
  const ref = m.alignment && m.alignment.offset != null;
  const rows = m.sections.map((g) => `<tr><td><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${labelColor(g.label)}"></span> ${esc(g.label)}${tag(g.method)}</td>
    <td>${tm(g.start)}–${tm(g.end)}</td>${ref ? `<td>${g.refStart === null ? "" : tm(g.refStart) + "–" + tm(g.refEnd)}</td>` : ""}
    <td>${pct(g.conf)}</td><td>${esc(g.method) || "—"}</td><td>${pct(g.boundaryConf)}</td></tr>`).join("");
  return `<table class="t"><tr><th>Section</th><th>File clock</th>${ref ? "<th>Spotify clock</th>" : ""}<th>Label conf</th><th>Method</th><th>Boundary conf</th></tr>${rows}</table>`;
}

function eventsHtml(m) {
  const rows = m.events.map((e) => `<tr><td>${esc(e.kind.replace("_", "-"))}</td><td>${tm(e.start)}${e.end != null ? "–" + tm(e.end) : ""}</td>
    <td>${pct(e.strength)}</td><td>${pct(e.conf)}</td></tr>`).join("") || `<tr><td colspan="4">none</td></tr>`;
  const ph = m.phrases.map((p) => `<tr><td>${esc(p.bars)}-bar phrase${tag(p.method)}</td><td>${tm(p.start)}–${tm(p.end)}</td><td></td><td>${pct(p.conf)}</td></tr>`).join("");
  return `<table class="t"><tr><th>Event</th><th>Time</th><th>Strength</th><th>Conf</th></tr>${rows}${ph}</table>`;
}

// "explicit" glyph: Material Symbols (Google), Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0
const EXPLICIT_ICON = `<svg viewBox="0 -960 960 960" width="18" height="18" fill="currentColor" aria-hidden="true" style="vertical-align:-4px"><path d="M360-280h240v-80H440v-80h160v-80H440v-80h160v-80H360v400ZM200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Z"/></svg>`;

const EVIDENCE_SOURCES = {
  caller: "caller's explicit flag", caller_match: "explicit flag of the matched track",
  youtube_age_limit: "YouTube age limit",
};
const evidenceText = (ev) =>
  ev.map((i) => `${EVIDENCE_SOURCES[i.source] || i.source}: ${JSON.stringify(i.value)}`).join("\n");

// Report only: never hides or dims anything.
function explicitHtml(m) {
  const x = m.explicit;
  if (!x) return "";
  const tip = escapeHtml(`method ${x.method}, confidence ${pct(x.conf)}` + (x.evidence.length ? "\nevidence:\n" + evidenceText(x.evidence) : ""));
  if (x.value === "yes") return `<div class="hint" title="${tip}"><span class="badge warn" style="font-size:13px">${EXPLICIT_ICON} Explicit</span> flagged (hover for evidence and source)</div>`;
  if (x.value === "no") return `<div class="hint dim" title="${tip}">explicit: not flagged</div>`;
  return `<div class="hint" title="${tip}">explicit: unknown</div>`;
}

const APPROX_NOTE = `<div class="note"><b>Approximate, not exact.</b> Analyses of a YouTube match are timing-approximate:
  the audio is not Spotify's own file, so beats, sections and events are exact only on this file's clock, and the
  Spotify clock is one offset that can drift. Section labels, phrases and meter are heuristic estimates.</div>`;
// A local file's analysis (ADR 0002): its own audio, so there is no match and no other clock.
const LOCAL_NOTE = `<div class="note"><b>Your local file.</b> Beats, sections and events are on this file's own
  clock. Section labels, phrases and meter are heuristic estimates.</div>`;
const isLocalResult = (d) => typeof (d && d.track_id) === "string" && d.track_id.startsWith("upl_");

// loadAudio: where the waveform gets its audio (default: the converted file; a picked local file
// for an upload, so the server never sends that audio back). opts.local marks a local file whose
// result has no upl_ id (the service page's v2 document); opts.via names the mode in the footer.
function renderAnalysis(d, loadAudio, opts) {
  const o = opts || {}, m = buildModel(d), local = o.local === true || isLocalResult(d);
  const via = o.via === "service" ? "service" : "standalone";
  tlModel = m;
  analysisCard.style.display = "block";
  analysisCard.innerHTML = `<div style="font-weight:600">Audio analysis</div>
    ${statsHtml(m)}${explicitHtml(m)}${tempoHtml(m)}${signalHtml(m)}${alignmentHtml(m, local)}
    <div class="viz-label">Waveform</div><div id="wfCard"></div>
    <div class="viz-label">Timeline</div>${timelineControls(m)}<div id="tlHost"></div>${timelineLegend(m)}${local ? LOCAL_NOTE : APPROX_NOTE}
    <div class="grid2"><div><div class="viz-label">Sections</div>${sectionsHtml(m)}</div>
    <div><div class="viz-label">Events &amp; phrases</div>${eventsHtml(m)}</div></div>
    <div class="foot">${escapeHtml(m.prov.analyzer)} analyzer ${escapeHtml(m.prov.version)} · document ${escapeHtml(m.prov.schema)} · ${via} analysis of ${local ? "a local file" : "a YouTube match"} · ${escapeHtml(m.prov.created)}</div>`;
  wireTimeline();
  startWaveform(m, loadAudio || loadTrackAudio);
}
window.addEventListener("resize", drawTimeline);
