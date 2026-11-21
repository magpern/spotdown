// Test helper (tests/test_ui_render.py): runs the page's scripts in a stubbed DOM and returns the
// HTML/SVG strings the analysis view builds for each result read from stdin. No browser, no network.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = process.argv[2];
const el = () => ({ style: {}, addEventListener() {}, set innerHTML(v) {}, value: "0" });
const ctx = { document: { getElementById: () => el() }, window: { addEventListener() {} }, location: { protocol: "file:" } };
vm.createContext(ctx);
for (const f of ["ui.js", "analysis_model.js", "analysis_timeline.js", "analysis_view.js",
  "waveform_model.js", "waveform_view.js", "waveform_player.js"])
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
ctx.results = JSON.parse(fs.readFileSync(0, "utf8"));
if (process.argv[3] === "call") {  // [{fn, args}] -> each call's result (the pure waveform model)
  const out = ctx.results.map((c) => vm.runInContext(c.fn, ctx)(...c.args));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}
// A stubbed page for the waveform player: elements by id (replacing #wfCard's html drops the old
// children, like a real DOM), a fake Web Audio whose decode, clock and sources the scenario drives.
function mkEnv(opts) {
  const els = {}, handlers = {}, env = { sources: [], ctxs: [], raf: null, interval: null, handlers };
  const get = (id) => (els[id] = els[id] || {
    style: {}, clientWidth: 900, innerHTML: "", textContent: "", value: "0", checked: false, disabled: false, attrs: {},
    addEventListener(ev, f) { (handlers[id] = handlers[id] || {})[ev] = f; },
    setAttribute(k, v) { this.attrs[k] = v; }, getBoundingClientRect() { return { left: 0 }; },
  });
  const card = get("wfCard");
  let cardHtml = "";
  Object.defineProperty(card, "innerHTML", { get: () => cardHtml, set: (v) => {
    cardHtml = v; for (const k of Object.keys(els)) if (k.startsWith("wf") && k !== "wfCard") delete els[k]; } });
  env.get = get;
  class AC {
    constructor() { this.currentTime = 0; this.destination = {}; env.ctxs.push(this); }
    decodeAudioData(raw) { env.decodes = (env.decodes || 0) + 1; return opts.decode(raw); }
    resume() {} close() { this.closed = true; }
    createBufferSource() { const n = { connect() {}, start(...a) { n.started = a; }, stop() { n.stopped = true; } }; env.sources.push(n); return n; }
    createOscillator() { const o = { frequency: {}, connect() {}, start() {}, stop() {} }; env.clicks = (env.clicks || 0) + 1; env.lastClick = o; return o; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  }
  const win = { addEventListener() {}, AudioContext: opts.noAudio ? undefined : AC };
  const c = { document: { getElementById: get }, window: win, location: { protocol: "file:" },
    requestAnimationFrame: (f) => { env.raf = f; return 1; }, cancelAnimationFrame() { env.raf = null; },
    setInterval: (f) => { env.interval = f; return 1; }, clearInterval() { env.interval = null; } };
  vm.createContext(c);
  for (const f of ["ui.js", "analysis_model.js", "analysis_timeline.js", "analysis_view.js", "waveform_model.js", "waveform_view.js", "waveform_player.js"])
    vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), c, { filename: f });
  env.ctx = c; env.ev = (code) => vm.runInContext(code, c);
  return env;
}
const deferred = () => { const d = {}; d.promise = new Promise((res, rej) => { d.resolve = res; d.reject = rej; }); return d; };
const fakeBuf = (duration, peak) => ({ duration, numberOfChannels: 1, getChannelData: () => Float32Array.from([peak, -peak / 2, peak / 4, 0]) });
const snap = (env) => ({ host: env.get("wfHost").innerHTML, dur: env.ev("wf.buf && wf.buf.duration"), peaks: env.ev("wf.peaks && wf.peaks[0]") });

if (process.argv[3] === "fallback") {  // [{document, ac, loader, msg}] -> the host's html after startWaveform
  (async () => {
    const out = [];
    for (const c of ctx.results) {
      const env = mkEnv({ noAudio: c.ac === "none", decode: () => Promise.reject(new Error("unable to decode")) });
      env.ctx.doc = c.document;
      env.ctx.loader = c.loader === "null" ? async () => null : c.loader === "throw" ? async () => { throw new Error(c.msg || "download failed"); } : async () => new ArrayBuffer(8);
      await env.ev("startWaveform(buildModel({ document: doc }), loader)");
      out.push({ host: env.get("wfHost").innerHTML, playDisabled: env.get("wfPlay").disabled });
    }
    process.stdout.write(JSON.stringify(out));
  })();
  return;
}
if (process.argv[3] === "stale") {  // two overlapping startWaveform calls; the older one finishes last
  (async () => {
    const doc = ctx.results[0].document, out = {};
    const run = async (olderOutcome, newerOutcome, stage) => {
      const dec = { A: deferred(), B: deferred() }, ld = { A: deferred(), B: deferred() };
      const env = mkEnv({ decode: (raw) => dec[raw.id].promise });
      env.ctx.doc = doc; env.ctx.ld = ld;
      const start = (k) => env.ev(`startWaveform(buildModel({ document: doc }), () => ld.${k}.promise)`);
      const pA = start("A");
      if (stage === "decode") { ld.A.resolve({ id: "A" }); await new Promise((r) => setImmediate(r)); }  // A is decoding when B starts
      const pB = start("B");  // B is the newer call
      const settle = async (k, outcome, buf) => {
        if (outcome === "ok") { ld[k].resolve({ id: k }); dec[k].resolve(buf); }
        else if (outcome === "none") ld[k].resolve(null);
        else ld[k].reject(new Error("older failure"));
      };
      await settle("B", newerOutcome, fakeBuf(20, 0.5)); await pB;
      const afterNewer = snap(env);
      if (stage === "decode") { if (olderOutcome === "ok") dec.A.resolve(fakeBuf(99, 1)); else dec.A.reject(new Error("older decode failure")); }
      else await settle("A", olderOutcome, fakeBuf(99, 1));
      await pA;  // the older call finishes LAST
      return { afterNewer, afterOlder: snap(env), play: env.get("wfPlay").disabled, decodes: env.decodes || 0 };
    };
    out.okThenOk = await run("ok", "ok");
    out.failAfterOk = await run("fail", "ok");
    out.noAudioAfterOk = await run("none", "ok");
    out.okAfterFail = await run("ok", "fail");
    out.decodeOkLast = await run("ok", "ok", "decode");
    out.decodeFailLast = await run("fail", "ok", "decode");
    process.stdout.write(JSON.stringify(out));
  })();
  return;
}
if (process.argv[3] === "play") {  // playback clock, end of track, decoded duration
  (async () => {
    const doc = ctx.results[0].document, env = mkEnv({ decode: async () => fakeBuf(10, 0.5) });
    env.ctx.doc = doc;
    await env.ev("startWaveform(buildModel({ document: doc }), async () => new ArrayBuffer(8))");
    const click = env.handlers.wfPlay.click, out = { time0: env.get("wfTime").textContent };
    click();  // play from 0
    const ac = env.ctxs[env.ctxs.length - 1];
    ac.currentTime = 4; env.raf();
    out.mid = env.get("wfTime").textContent;
    ac.currentTime = 12; env.raf();  // past the decoded end (10 s), though the analysis says 40 s
    out.end = { time: env.get("wfTime").textContent, playing: env.ev("wf.playing"), pos: env.ev("wf.pos"),
      label: env.get("wfPlay").textContent };
    click();  // play again after the end: starts from 0
    out.again = { pos: env.ev("wf.pos"), started: env.sources[env.sources.length - 1].started };
    out.analysisDuration = env.ev("wf.m.duration");
    process.stdout.write(JSON.stringify(out));
  })();
  return;
}
if (process.argv[3] === "waveform") {  // [document result] -> {svg, legend} for a view
  const out = ctx.results.map((d) => vm.runInContext(`(() => { const m = buildModel(${JSON.stringify(d)});
    const v = { t0: 0, t1: m.duration, w: 900 };
    return waveformSvg(wfColumns(wfPeaks([[0.1, -0.5, 0.2, 0.9]], 4), m.duration, v), wfMarkers(m, v), v) + waveformLegend(); })()`, ctx));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}
const out = vm.runInContext(`results.map((d) => {
  const m = buildModel(d);
  return [statsHtml(m), explicitHtml(m), tempoHtml(m), signalHtml(m), alignmentHtml(m),
    sectionsHtml(m), eventsHtml(m), timelineSvg(m, 0, m.duration, 900),
    timelineSvg(m, 10, 30, 900), timelineLegend(m)].join("\\n");
})`, ctx);
process.stdout.write(JSON.stringify(out));
