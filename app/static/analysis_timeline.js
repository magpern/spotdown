"use strict";
// Dependency-free inline SVG timeline of an analysis model (see analysis_model.js).
const LABEL_COLORS = {
  intro: "#6b7280", verse: "#3b82f6", chorus: "#1db954", bridge: "#f59e0b",
  breakdown: "#a78bfa", outro: "#6b7280", unlabelled: "#4b5563",
};
const labelColor = (l) => LABEL_COLORS[l] || "#3b82f6";
const GUTTER = 64;
const ROWS = { axis: [0, 16], sec: [18, 30], phr: [50, 18], evt: [70, 30], en: [102, 64], beat: [168, 30] };
const TL_H = 200;
const f1 = (n) => n.toFixed(1);

function timelineSvg(m, t0, t1, W) {
  const span = t1 - t0, plot = W - GUTTER - 4;
  const x = (t) => GUTTER + ((t - t0) / span) * plot;
  const vis = (a, b) => b >= t0 && a <= t1;
  const bottom = (r) => ROWS[r][0] + ROWS[r][1];
  let s = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<line x1="0" y1="0" x2="0" y2="6" stroke="#000" stroke-width="2" opacity=".35"/></pattern>` +
    `<clipPath id="clipw"><rect x="${GUTTER}" y="0" width="${plot}" height="${TL_H}"/></clipPath></defs>`;
  const names = { axis: "", sec: "sections", phr: "phrases", evt: "events", en: "energy", beat: "beats" };
  for (const r in names) s += `<text x="2" y="${ROWS[r][0] + 11}">${names[r]}</text>`;
  s += `<g clip-path="url(#clipw)">`;
  // axis
  const stepT = span > 240 ? 30 : span > 90 ? 10 : span > 30 ? 5 : 1;
  for (let t = Math.ceil(t0 / stepT) * stepT; t <= t1; t += stepT) {
    s += `<line x1="${f1(x(t))}" y1="12" x2="${f1(x(t))}" y2="${TL_H}" stroke="#2a2f37" stroke-width="1"/>` +
      `<text x="${f1(x(t)) + 2}" y="10">${fmtDur(Math.round(t))}</text>`;
  }
  // silence and fades
  for (const r of m.silences) if (vis(r.start_s, r.end_s))
    s += `<rect x="${f1(x(r.start_s))}" y="14" width="${f1(x(r.end_s) - x(r.start_s))}" height="${TL_H - 14}" fill="#6b7280" opacity=".22"><title>silence ${r.start_s}–${r.end_s}s</title></rect>`;
  for (const [f, nm] of [[m.fadeIn, "fade in"], [m.fadeOut, "fade out"]]) if (f && vis(f.start_s, f.end_s))
    s += `<rect x="${f1(x(f.start_s))}" y="${ROWS.en[0]}" width="${f1(x(f.end_s) - x(f.start_s))}" height="${ROWS.en[1]}" fill="#f59e0b" opacity=".18"><title>${nm} ${f.start_s}–${f.end_s}s (conf ${f.confidence})</title></rect>`;
  // sections (hatched = heuristic label)
  for (const g of m.sections) {
    if (!vis(g.start, g.end)) continue;
    const [y, h] = ROWS.sec, gx = x(g.start), gw = Math.max(x(g.end) - gx, 1);
    const tip = `${g.label}${g.conf !== null ? " conf " + g.conf + " (" + g.method + ")" : ""} · boundary conf ${g.boundaryConf} · ${g.start}–${g.end}s`;
    s += `<rect x="${f1(gx)}" y="${y}" width="${f1(gw)}" height="${h}" fill="${labelColor(g.label)}" opacity=".85" stroke="#0e1013"><title>${escapeHtml(tip)}</title></rect>`;
    if (g.method === "heuristic") s += `<rect x="${f1(gx)}" y="${y}" width="${f1(gw)}" height="${h}" fill="url(#hatch)" pointer-events="none"/>`;
    if (gw > 70) s += `<text class="on" x="${f1(gx) + 4}" y="${y + 19}">${g.label}${g.conf !== null ? " " + g.conf.toFixed(2) : ""}</text>`;
  }
  // phrases
  for (const p of m.phrases) {
    if (!vis(p.start_s, p.end_s)) continue;
    const [y] = ROWS.phr, a = x(p.start_s) + 1, b = x(p.end_s) - 1, c = p.bars === 16 ? "#f472b6" : "#22d3ee";
    s += `<path d="M${f1(a)},${y + 14} V${y + 6} H${f1(b)} V${y + 14}" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="${p.method === "heuristic" ? "4,2" : "none"}"><title>${p.bars}-bar phrase conf ${p.confidence} (${p.method})</title></path>`;
    if (b - a > 26) s += `<text x="${f1((a + b) / 2 - 8)}" y="${y + 4}">${p.bars} bar</text>`;
  }
  // events
  for (const e of m.events) {
    if (!vis(e.start_s, e.end_s || e.start_s)) continue;
    const [y, h] = ROWS.evt, ex = x(e.start_s), sz = 5 + 7 * e.strength;
    const tip = `${e.kind} strength ${e.strength} conf ${e.confidence} @ ${e.start_s}s`;
    if (e.kind === "build_up") {
      s += `<rect x="${f1(ex)}" y="${y + h - 4 - 16 * e.strength}" width="${f1(x(e.end_s) - ex)}" height="${4 + 16 * e.strength}" fill="#f59e0b" opacity=".7"><title>${tip}</title></rect>`;
    } else if (e.kind === "drop") {
      s += `<path d="M${f1(ex - sz)},${y + 2} h${2 * sz} l${-sz},${sz + 6} z" fill="#1db954"><title>${tip}</title></path>` +
        `<line x1="${f1(ex)}" y1="${ROWS.en[0]}" x2="${f1(ex)}" y2="${bottom("en")}" stroke="#1db954" stroke-dasharray="3,2"/>`;
    } else {
      s += `<path d="M${f1(ex)},${y + 2} l${sz},${sz} l${-sz},${sz} l${-sz},${-sz} z" fill="#a78bfa"><title>${tip}</title></path>`;
    }
    s += `<text x="${f1(ex) + 2}" y="${y + h}">${e.strength.toFixed(2)}</text>`;
  }
  // energy
  const [ey, eh] = ROWS.en, en = m.energy;
  const path = (vals, area) => {
    let d = "";
    vals.forEach((v, i) => { d += `${i ? "L" : "M"}${f1(x(i * en.step))},${f1(ey + eh - v * eh)} `; });
    return area ? `M${f1(x(0))},${ey + eh} ` + d.replace(/^M/, "L") + `L${f1(x((vals.length - 1) * en.step))},${ey + eh}Z` : d;
  };
  s += `<path d="${path(en.perc, true)}" fill="#f59e0b" opacity=".2"/>` +
    `<path d="${path(en.perc, false)}" fill="none" stroke="#f59e0b" stroke-width="1.2"/>` +
    `<path d="${path(en.low, false)}" fill="none" stroke="#f472b6" stroke-width="1.2" stroke-dasharray="4,2"/>` +
    `<path d="${path(en.full, false)}" fill="none" stroke="#60a5fa" stroke-width="1.5"/>`;
  // beats, downbeats (bar lines)
  const [by, bh] = ROWS.beat;
  let ticks = "";
  for (const t of m.beats) if (t >= t0 && t <= t1) ticks += `M${f1(x(t))},${by + 12} V${by + bh - 2} `;
  s += `<path d="${ticks}" stroke="#9aa3af" stroke-width="1" opacity=".7"/>`;
  if (m.downbeats) {
    const barPx = (m.downbeats.length > 1 ? m.downbeats[1] - m.downbeats[0] : 2) / span * plot;
    const every = [1, 2, 4, 8, 16, 32].find((n) => n * barPx >= 26) || 32;
    let bars = "";
    m.downbeats.forEach((t, i) => {
      if (t < t0 || t > t1) return;
      bars += `M${f1(x(t))},${by + 4} V${by + bh} `;
      if (i % every === 0) s += `<text x="${f1(x(t)) + 2}" y="${by + 10}">${i + 1}</text>`;
    });
    s += `<path d="${bars}" stroke="#1db954" stroke-width="1.6"/>`;
    s += `<path d="${m.downbeats.filter((t) => t >= t0 && t <= t1).map((t) => `M${f1(x(t))},14 V${by}`).join(" ")}" stroke="#1db954" stroke-width=".5" opacity=".25"/>`;
  }
  // content edges
  for (const t of [m.contentStart, m.contentEnd]) if (vis(t, t))
    s += `<line x1="${f1(x(t))}" y1="14" x2="${f1(x(t))}" y2="${TL_H}" stroke="#fff" stroke-width="1" stroke-dasharray="2,3" opacity=".7"/>`;
  return `<svg id="timeline" viewBox="0 0 ${W} ${TL_H}" width="${W}" height="${TL_H}" style="display:block">${s}</g></svg>`;
}

function timelineLegend(m) {
  const labels = [...new Set(m.sections.map((g) => g.label))];
  const sw = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
  return `<div class="legend">` + labels.map((l) => sw(labelColor(l), l)).join("") +
    `<span class="tag">hatched = heuristic label</span>` +
    sw("#f59e0b", "percussive / build-up") + sw("#60a5fa", "full energy") + sw("#f472b6", "low band") +
    sw("#1db954", "drop · bar line") + sw("#a78bfa", "breakdown") + sw("#9aa3af", "beat") +
    sw("#22d3ee", "8-bar phrase") + sw("#f472b6", "16-bar phrase") + sw("#fff", "content start/end") + sw("#6b7280", "silence") + `</div>`;
}
