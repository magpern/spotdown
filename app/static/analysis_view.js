"use strict";
// The "Audio analysis" card: stats, timeline with a window control, and the detail tables.
let tlModel = null, tlWindow = { start: 0, len: 0 };  // len 0 = whole track

const pct = (v) => (v === null || v === undefined ? "—" : v.toFixed(2));
const tm = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const tag = (method) => (method === "heuristic" ? `<span class="tag">heuristic</span>` : "");

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
  const meter = m.meter ? `${m.meter.beats_per_bar}/4` : "—";
  const cells = [
    [m.tempo.bpm, "BPM"], [meter, "Meter"], [fmtDur(Math.round(m.duration)), "Length"],
    [m.drops.length, "Drops"], [m.sections.length, "Sections"], [m.phrases.length, "Phrases"],
  ];
  return `<div class="stats">` + cells.map(([v, l]) =>
    `<div class="stat"><div class="sv">${v}</div><div class="sl">${l}</div></div>`).join("") + `</div>`;
}

function tempoHtml(m) {
  const cand = m.tempo.candidates.map((c) => `${c.bpm} (${pct(c.confidence)})`).join(", ");
  const meter = m.meter
    ? `${m.meter.beats_per_bar}/4 conf ${pct(m.meter.confidence)} ${tag(m.meter.method)}`
    : "not claimed (gate not passed)";
  const bars = m.downbeats ? `${m.downbeats.length} bars, downbeat conf ${pct(m.downbeatConf)}` : "no downbeats";
  return `<div class="hint">Tempo ${m.tempo.bpm} BPM, confidence ${pct(m.tempo.conf)}, stability ${pct(m.tempo.stability)}.
    Half/double-time candidates: ${cand}.<br>Meter ${meter} · ${m.beats.length} beats · ${bars}.</div>`;
}

function signalHtml(m) {
  const reg = m.silences.map((r) => `${tm(r.start_s)}–${tm(r.end_s)}`).join(", ") || "none";
  const fade = (f, n) => (f ? `${n} ${tm(f.start_s)}–${tm(f.end_s)} (conf ${pct(f.confidence)})` : `no ${n}`);
  return `<div class="hint">Content ${tm(m.contentStart)}–${tm(m.contentEnd)} of ${tm(m.duration)} ·
    silence: ${reg} · ${fade(m.fadeIn, "fade in")}, ${fade(m.fadeOut, "fade out")}.</div>`;
}

function alignmentHtml(m) {
  const a = m.alignment;
  if (!a) return `<div class="hint">Alignment: none (no Spotify duration known).</div>`;
  if (a.result !== "ok") {
    const h = a.hypotheses.map((x) => `${escapeHtml(x.name)} residual ${x.residual_s}s`).join("; ");
    return `<div class="hint"><span class="badge warn">alignment failed</span> ${h}. Times are file-clock only.</div>`;
  }
  return `<div class="hint"><span class="badge ok">aligned</span> Spotify time = file time ${a.offset >= 0 ? "+" : "−"} ${Math.abs(a.offset)}s ·
    residual ${a.residual}s · confidence ${pct(a.conf)} · Spotify duration ${a.refDuration}s.</div>`;
}

function sectionsHtml(m) {
  const ref = m.alignment && m.alignment.offset !== undefined;
  const rows = m.sections.map((g) => `<tr><td><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${labelColor(g.label)}"></span> ${escapeHtml(g.label)}${tag(g.method)}</td>
    <td>${tm(g.start)}–${tm(g.end)}</td>${ref ? `<td>${g.refStart === null ? "" : tm(g.refStart) + "–" + tm(g.refEnd)}</td>` : ""}
    <td>${pct(g.conf)}</td><td>${g.method || "—"}</td><td>${pct(g.boundaryConf)}</td></tr>`).join("");
  return `<table class="t"><tr><th>Section</th><th>File clock</th>${ref ? "<th>Spotify clock</th>" : ""}<th>Label conf</th><th>Method</th><th>Boundary conf</th></tr>${rows}</table>`;
}

function eventsHtml(m) {
  const rows = m.events.map((e) => `<tr><td>${e.kind.replace("_", "-")}</td><td>${tm(e.start_s)}${e.end_s !== undefined ? "–" + tm(e.end_s) : ""}</td>
    <td>${pct(e.strength)}</td><td>${pct(e.confidence)}</td></tr>`).join("") || `<tr><td colspan="4">none</td></tr>`;
  const ph = m.phrases.map((p) => `<tr><td>${p.bars}-bar phrase${tag(p.method)}</td><td>${tm(p.start_s)}–${tm(p.end_s)}</td><td></td><td>${pct(p.confidence)}</td></tr>`).join("");
  return `<table class="t"><tr><th>Event</th><th>Time</th><th>Strength</th><th>Conf</th></tr>${rows}${ph}</table>`;
}

const APPROX_NOTE = `<div class="note"><b>Approximate, not exact.</b> Analyses of a YouTube match are timing-approximate:
  the audio is not Spotify's own file, so beats, sections and events are exact only on this file's clock, and the
  Spotify clock is one offset that can drift. Section labels, phrases and meter are heuristic estimates.</div>`;

function renderAnalysis(d) {
  const m = buildModel(d);
  tlModel = m;
  analysisCard.style.display = "block";
  analysisCard.innerHTML = `<div style="font-weight:600">Audio analysis</div>
    ${statsHtml(m)}${tempoHtml(m)}${signalHtml(m)}${alignmentHtml(m)}
    <div class="viz-label">Timeline</div>${timelineControls(m)}<div id="tlHost"></div>${timelineLegend(m)}${APPROX_NOTE}
    <div class="grid2"><div><div class="viz-label">Sections</div>${sectionsHtml(m)}</div>
    <div><div class="viz-label">Events &amp; phrases</div>${eventsHtml(m)}</div></div>
    <div class="foot">${escapeHtml(m.prov.analyzer)} analyzer ${escapeHtml(m.prov.version)} · document ${escapeHtml(m.prov.schema)} · ${escapeHtml(m.prov.created)}</div>`;
  wireTimeline();
}
window.addEventListener("resize", drawTimeline);
