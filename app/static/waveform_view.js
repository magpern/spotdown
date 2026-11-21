"use strict";
// Waveform renderer: builds the SVG string for one view (see waveform_model.js). No DOM access.
// Every label from the document goes through escapeHtml (ui.js).
const WF_H = 150, WF_BAND = [0, 22], WF_WAVE = [26, 100], WF_TICK_Y = 130;
const WF_COLORS = { build: "#f59e0b", drop: "#1db954", beat: "#9aa3af", bar: "#1db954", wave: "#60a5fa" };
const wf1 = (n) => n.toFixed(1);

function waveformSvg(cols, mk, v) {
  const mid = WF_WAVE[0] + WF_WAVE[1] / 2, half = WF_WAVE[1] / 2;
  let s = `<defs><pattern id="wfhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<line x1="0" y1="0" x2="0" y2="6" stroke="#000" stroke-width="2" opacity=".35"/></pattern></defs>`;
  for (const b of mk.buildUps)
    s += `<rect x="${wf1(b.x)}" y="${WF_WAVE[0]}" width="${wf1(b.w)}" height="${WF_WAVE[1]}" fill="${WF_COLORS.build}" opacity=".28">` +
      `<title>${escapeHtml(`build-up into a drop, strength ${pct(b.strength)}, ${b.start}–${b.end}s`)}</title></rect>`;
  let wave = "";
  cols.forEach((c, i) => { if (c > 0) wave += `M${i + 0.5},${wf1(mid - c * half)} V${wf1(mid + c * half)} `; });
  s += `<path d="${wave}" stroke="${WF_COLORS.wave}" stroke-width="1" fill="none"/>`;
  for (const g of mk.sections) {
    const tip = `${g.label}${g.conf !== null ? " conf " + pct(g.conf) : ""}${g.heuristic ? " (heuristic)" : ""} · ${g.start}–${g.end}s`;
    s += `<rect x="${wf1(g.x)}" y="${WF_BAND[0]}" width="${wf1(Math.max(g.w, 1))}" height="${WF_BAND[1]}" fill="${labelColor(g.label)}" opacity=".85" stroke="#0e1013">` +
      `<title>${escapeHtml(tip)}</title></rect>`;
    if (g.heuristic) s += `<rect x="${wf1(g.x)}" y="${WF_BAND[0]}" width="${wf1(Math.max(g.w, 1))}" height="${WF_BAND[1]}" fill="url(#wfhatch)" pointer-events="none"/>`;
    if (g.w > 60) s += `<text class="on" x="${wf1(g.x) + 4}" y="${WF_BAND[0] + 15}">${escapeHtml(g.label)}${g.conf !== null ? " " + pct(g.conf) : ""}</text>`;
  }
  for (const d of mk.drops)
    s += `<line x1="${wf1(d.x)}" y1="${WF_WAVE[0]}" x2="${wf1(d.x)}" y2="${WF_TICK_Y}" stroke="${WF_COLORS.drop}" stroke-width="2" stroke-dasharray="4,2"/>` +
      `<path d="M${wf1(d.x - 6)},${WF_WAVE[0]} h12 l-6,9 z" fill="${WF_COLORS.drop}"><title>${escapeHtml(`drop @ ${d.t}s, strength ${pct(d.strength)}`)}</title></path>`;
  const ticks = (xs, y, color, width) => (xs.length ? `<path d="${xs.map((x) => `M${wf1(x)},${y} V${WF_H}`).join(" ")}" stroke="${color}" stroke-width="${width}"/>` : "");
  s += ticks(mk.beats, WF_TICK_Y + 8, WF_COLORS.beat, 1) + ticks(mk.downbeats, WF_TICK_Y, WF_COLORS.bar, 2);
  if (mk.beatsHidden) s += `<text x="4" y="${WF_H - 2}">beats hidden: zoom in</text>`;
  const span = v.t1 - v.t0, step = span > 240 ? 30 : span > 90 ? 10 : span > 30 ? 5 : 1;
  for (let t = Math.ceil(v.t0 / step) * step; t <= v.t1; t += step)
    s += `<text x="${wf1(wfX(v, t)) + 2}" y="${WF_H - 14}">${fmtDur(Math.round(t))}</text>`;
  s += `<line id="wfHead" x1="-5" y1="0" x2="-5" y2="${WF_H}" stroke="#fff" stroke-width="1.5"/>`;
  return `<svg id="waveform" viewBox="0 0 ${v.w} ${WF_H}" width="${v.w}" height="${WF_H}" style="display:block;cursor:pointer">${s}</svg>`;
}

function waveformLegend() {
  const sw = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
  return `<div class="legend"><span class="tag">hatched = heuristic label</span>` +
    sw(WF_COLORS.build, "build-up into a drop") + sw(WF_COLORS.drop, "drop") +
    sw(WF_COLORS.bar, "downbeat (bar line)") + sw(WF_COLORS.beat, "beat") + `</div>`;
}
