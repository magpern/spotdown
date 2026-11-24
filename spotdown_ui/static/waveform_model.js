"use strict";
// Waveform model: peaks, the time/pixel mapping, beat thinning, marker positions and the click
// schedule. Pure (no DOM, no Web Audio, no network) so tests/ui_harness.js can call it directly.
// It reads the view model of analysis_model.js (sections, events, beats), never the raw document.
const WF_BUCKETS = 20000;      // peak resolution of the whole track
const WF_MIN_TICK_PX = 5;      // beats closer than this are thinned or hidden

// Largest absolute sample per bucket, over all channels (arrays or typed arrays).
function wfPeaks(channels, buckets) {
  const out = new Array(buckets).fill(0);
  const n = Math.max(0, ...channels.map((c) => c.length));
  if (!n) return out;
  for (let b = 0; b < buckets; b++) {
    const s = Math.floor((b * n) / buckets), e = Math.min(n, Math.max(Math.floor(((b + 1) * n) / buckets), s + 1));
    let peak = 0;
    for (const ch of channels) for (let i = s; i < e && i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > peak) peak = a;
    }
    out[b] = peak;
  }
  return out;
}

// A view is { t0, t1, w }: the seconds shown and the pixel width they are drawn into.
const wfX = (v, t) => ((t - v.t0) / (v.t1 - v.t0)) * v.w;
const wfT = (v, x) => v.t0 + (x / v.w) * (v.t1 - v.t0);

// One value per pixel column of the view: the largest bucket peak that column covers, scaled so
// the loudest peak of the whole track is 1. Columns outside [0, dur] are 0.
function wfColumns(peaks, dur, v) {
  const B = peaks.length, top = Math.max(...peaks) || 1, cols = [];
  for (let c = 0; c < v.w; c++) {
    const ta = wfT(v, c), tb = wfT(v, c + 1);
    if (tb <= 0 || ta >= dur) { cols.push(0); continue; }
    const a = Math.max(Math.floor((Math.max(ta, 0) / dur) * B), 0);
    const e = Math.min(Math.max(Math.ceil((Math.min(tb, dur) / dur) * B), a + 1), B);
    let m = 0;
    for (let i = a; i < e; i++) if (peaks[i] > m) m = peaks[i];
    cols.push(m / top);
  }
  return cols;
}

// Beat ticks for a view: every beat while they are far enough apart, else bar lines only, else
// none (hidden = true). Downbeats are the strong ticks; a beat that is a downbeat is not repeated.
function wfBeatTicks(beats, downbeats, v) {
  const key = (t) => Math.round(t * 1000), inView = (t) => t >= v.t0 && t <= v.t1;
  const pxPerSec = v.w / (v.t1 - v.t0);
  const interval = beats.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 0;
  const strongSet = new Set((downbeats || []).map(key));
  const strong = (downbeats || []).filter(inView);
  const barGap = downbeats && downbeats.length > 1 ? (downbeats[downbeats.length - 1] - downbeats[0]) / (downbeats.length - 1) : 0;
  if (interval > 0 && interval * pxPerSec >= WF_MIN_TICK_PX)
    return { beats: beats.filter((t) => inView(t) && !strongSet.has(key(t))), downbeats: strong, hidden: false };
  if (barGap > 0 && barGap * pxPerSec >= WF_MIN_TICK_PX) return { beats: [], downbeats: strong, hidden: false };
  return { beats: [], downbeats: [], hidden: true };
}

// Pixel positions of everything drawn over the waveform. Only sections, drops and build-ups
// (the rise into a detected drop, ADR 0001) are read from the analysis; other events are ignored.
function wfMarkers(m, v) {
  const clip = (a, b) => ({ a: Math.max(a, v.t0), b: Math.min(b, v.t1) });
  const sections = [];
  for (const g of m.sections) {
    const { a, b } = clip(g.start, g.end);
    if (b <= a) continue;
    sections.push({ x: wfX(v, a), w: wfX(v, b) - wfX(v, a), label: g.label, conf: g.conf,
      heuristic: g.method === "heuristic", start: g.start, end: g.end });
  }
  const drops = m.events.filter((e) => e.kind === "drop" && e.start >= v.t0 && e.start <= v.t1)
    .map((e) => ({ x: wfX(v, e.start), strength: e.strength, t: e.start }));
  const buildUps = [];
  for (const e of m.events) {
    if (e.kind !== "build_up") continue;
    const end = e.end === null ? e.start : e.end, { a, b } = clip(e.start, end);
    if (b < a || end < v.t0 || e.start > v.t1) continue;
    buildUps.push({ x: wfX(v, a), w: Math.max(wfX(v, b) - wfX(v, a), 2), strength: e.strength, start: e.start, end });
  }
  const ticks = wfBeatTicks(m.beats, m.downbeats, v);
  return { sections, drops, buildUps,
    beats: ticks.beats.map((t) => wfX(v, t)), downbeats: ticks.downbeats.map((t) => wfX(v, t)),
    beatsHidden: ticks.hidden };
}

// The only audio URL the page fetches: a /files/<name> path on its own origin (the standalone
// download). Returns that path, or null for anything else (another origin, another path, traversal).
function wfAudioPath(url, origin) {
  if (typeof url !== "string") return null;
  let p = url;
  if (!url.startsWith("/")) {
    const m = /^(https?:\/\/[^/?#]+)(\/.*)$/i.exec(url);
    if (!m || m[1].toLowerCase() !== String(origin).toLowerCase()) return null;
    p = m[2];
  }
  return /^\/files\/[^/?#\\]+$/.test(p) && !p.includes("..") ? p : null;
}

// Clicks to sound in [a, b): every beat, the downbeats flagged strong.
function wfClicksBetween(beats, downbeats, a, b) {
  const key = (t) => Math.round(t * 1000), strong = new Set((downbeats || []).map(key));
  return beats.filter((t) => t >= a && t < b).map((t) => ({ t, strong: strong.has(key(t)) }));
}
