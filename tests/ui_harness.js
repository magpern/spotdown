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
if (process.argv[3] === "fallback") {  // [{document, ac, loader}] -> the host's html after startWaveform
  (async () => {
    const out = [];
    for (const c of ctx.results) {
      const els = {}, get = (id) => (els[id] = els[id] || { style: {}, innerHTML: "", addEventListener() {}, setAttribute() {} });
      const AC = c.ac === "none" ? undefined : class { decodeAudioData() { return Promise.reject(new Error("unable to decode")); } close() {} };
      const c2 = { document: { getElementById: get }, window: { addEventListener() {}, AudioContext: AC }, location: { protocol: "file:" },
        cancelAnimationFrame() {}, clearInterval() {} };
      c2.loader = c.loader === "null" ? async () => null : c.loader === "throw" ? async () => { throw new Error("download failed"); } : async () => new ArrayBuffer(8);
      vm.createContext(c2);
      for (const f of ["ui.js", "analysis_model.js", "analysis_timeline.js", "waveform_model.js", "waveform_view.js", "waveform_player.js"])
        vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), c2, { filename: f });
      c2.doc = c.document;
      await vm.runInContext("startWaveform(buildModel({ document: doc }), loader)", c2);
      out.push({ host: get("wfHost").innerHTML, card: get("wfCard").innerHTML, playDisabled: get("wfPlay").disabled });
    }
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
