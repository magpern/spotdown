"use strict";
// Waveform card behaviour: decode the track in the browser (Web Audio decodeAudioData, from audio
// the page already has), draw it, play/pause with a playhead, click-to-seek, zoom and an optional
// click track on the beats. Never blocks the page: any failure leaves the timeline and a message.
const wf = { gen: 0, ctx: null, buf: null, peaks: null, m: null, win: { start: 0, len: 0 },
  pos: 0, playing: false, src: null, t0: 0, raf: 0, timer: 0, nextClick: 0 };

// The decoded buffer's own length is the clock of everything heard and drawn; the analysis duration
// is only the fallback before decoding (the two can differ by a frame or two).
const wfDur = () => (wf.buf ? wf.buf.duration : wf.m.duration);

function wfMessage(html) { const el = $("wfHost"); if (el) el.innerHTML = `<div class="hint">${html}</div>`; }

function wfStop() {
  if (wf.src) { try { wf.src.stop(); } catch (e) { /* already stopped */ } wf.src = null; }
  cancelAnimationFrame(wf.raf); clearInterval(wf.timer);
  wf.playing = false;
  if (wf.ctx) { try { wf.ctx.close(); } catch (e) { /* closing is best effort */ } wf.ctx = null; }
}

function wfView() {
  const el = $("wfHost"), W = Math.max(el.clientWidth, 320), dur = wfDur();
  const len = wf.win.len || dur, t0 = Math.min(wf.win.start, Math.max(dur - len, 0));
  return { t0, t1: Math.min(t0 + len, dur), w: W };
}

function wfDraw() {
  if (!wf.buf || !$("wfHost")) return;
  const v = wfView();
  $("wfHost").innerHTML = waveformSvg(wfColumns(wf.peaks, wfDur(), v), wfMarkers(wf.m, v), v);
  wfHead();
}

function wfHead(pos = wf.pos) {
  const head = $("wfHead"), v = wf.buf && $("wfHost") ? wfView() : null;
  if (!head || !v) return;
  const x = wfX(v, pos);
  head.setAttribute("x1", x); head.setAttribute("x2", x);
  head.setAttribute("opacity", pos >= v.t0 && pos <= v.t1 ? 1 : 0);
  $("wfTime").textContent = `${tm(pos)} / ${tm(wfDur())}`;
}

function wfClock() { return wf.playing ? wf.pos + (wf.ctx.currentTime - wf.t0) : wf.pos; }

function wfClicks() {
  if (!wf.playing || !$("wfClick").checked) { wf.nextClick = wfClock(); return; }
  const now = wfClock(), until = now + 0.3;
  for (const c of wfClicksBetween(wf.m.beats, wf.m.downbeats, Math.max(wf.nextClick, now), until)) {
    const o = wf.ctx.createOscillator(), g = wf.ctx.createGain(), at = wf.t0 + (c.t - wf.pos);
    o.frequency.value = c.strong ? 1600 : 1000;
    g.gain.setValueAtTime(0.4, at); g.gain.exponentialRampToValueAtTime(0.001, at + 0.04);
    o.connect(g); g.connect(wf.ctx.destination); o.start(at); o.stop(at + 0.05);
  }
  wf.nextClick = until;
}

function wfTick() {
  if (!wf.playing) return;
  const now = wfClock();
  if (now >= wfDur()) return wfPause(0);  // the end: stop and rewind
  const v = wfView();
  if (wf.win.len && (now > v.t1 || now < v.t0)) { wf.win.start = Math.max(now - 1, 0); $("wfStart").value = wf.win.start; wfDraw(); }
  wfHead(now);
  wf.raf = requestAnimationFrame(wfTick);
}

function wfPlay(from) {
  if (!wf.buf) return;
  if (wf.src) { try { wf.src.stop(); } catch (e) { /* already stopped */ } }
  wf.ctx = wf.ctx || new (window.AudioContext || window.webkitAudioContext)();
  wf.ctx.resume();
  wf.pos = from; wf.t0 = wf.ctx.currentTime;
  wf.src = wf.ctx.createBufferSource(); wf.src.buffer = wf.buf; wf.src.connect(wf.ctx.destination);
  wf.src.start(0, from);
  wf.playing = true; wf.nextClick = from; $("wfPlay").textContent = "Pause";
  clearInterval(wf.timer); wf.timer = setInterval(wfClicks, 50);
  cancelAnimationFrame(wf.raf); wf.raf = requestAnimationFrame(wfTick);
}

// to: the position to rest at (the end of the track rewinds to 0); default is where playback is.
function wfPause(to) {
  if (to !== undefined) wf.pos = to;
  else if (wf.playing) wf.pos = Math.min(wfClock(), wfDur());
  if (wf.src) { try { wf.src.stop(); } catch (e) { /* already stopped */ } wf.src = null; }
  wf.playing = false; clearInterval(wf.timer); cancelAnimationFrame(wf.raf);
  $("wfPlay").textContent = "Play"; wfHead();
}

function wfControls() {
  const opts = [[0, "whole track"], [20, "20 s"], [40, "40 s"], [80, "80 s"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
  return `<div class="tl-ctl"><button id="wfPlay" type="button">Play</button>` +
    `<label style="display:inline;margin:0"><input id="wfClick" type="checkbox"> click on beats</label>` +
    `<span>Zoom</span><select id="wfLen">${opts}</select>` +
    `<input id="wfStart" type="range" min="0" max="${Math.floor(wf.m.duration)}" value="0" disabled><span id="wfTime"></span></div>`;
}

function wfWire() {
  $("wfPlay").addEventListener("click", () => (wf.playing ? wfPause() : wfPlay(wf.pos >= wfDur() ? 0 : wf.pos)));
  $("wfLen").addEventListener("change", () => { wf.win = { start: +$("wfStart").value, len: +$("wfLen").value }; $("wfStart").disabled = !wf.win.len; wfDraw(); });
  $("wfStart").addEventListener("input", () => { wf.win.start = +$("wfStart").value; wfDraw(); });
  $("wfHost").addEventListener("click", (e) => {
    const svg = $("waveform");
    if (!svg) return;
    const v = wfView(), t = Math.min(Math.max(wfT(v, e.clientX - svg.getBoundingClientRect().left), 0), wfDur());
    if (wf.playing) wfPlay(t); else { wf.pos = t; wfHead(); }
  });
}
window.addEventListener("resize", wfDraw);

// loadAudio: a function returning a Promise of an ArrayBuffer (or null when the page has no audio).
// The card is drawn empty-handed (timeline below stays) and the waveform fills in once decoded.
async function startWaveform(m, loadAudio) {
  wfStop();
  const gen = ++wf.gen, current = () => gen === wf.gen;  // a newer call makes this one stale
  Object.assign(wf, { m, buf: null, peaks: null, win: { start: 0, len: 0 }, pos: 0 });
  const card = $("wfCard");
  if (!card) return;
  card.innerHTML = `${wfControls()}<div id="wfHost"></div>${waveformLegend()}`;
  $("wfPlay").disabled = true;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error("this browser has no Web Audio");
    wfMessage('<span class="spin"></span>Decoding audio…');
    const raw = await loadAudio();
    if (!current()) return;  // nothing below may touch shared state or the page once stale
    if (!raw) throw new Error("this page has no audio for the track (convert it first)");
    const probe = new AC();
    let buf;
    try { buf = await probe.decodeAudioData(raw); } finally { probe.close(); }
    if (!current()) return;
    const peaks = wfPeaks(Array.from({ length: buf.numberOfChannels }, (_, i) => buf.getChannelData(i)), WF_BUCKETS);
    wf.buf = buf; wf.peaks = peaks;
    $("wfPlay").disabled = false;
    wfWire(); wfDraw();
  } catch (e) {
    if (!current()) return;
    wf.buf = null;
    wfMessage(`No waveform: ${escapeHtml(e.message)}. The timeline below is unaffected.`);
  }
}
