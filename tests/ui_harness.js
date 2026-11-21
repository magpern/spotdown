// Test helper (tests/test_ui_render.py): runs the page's scripts in a stubbed DOM and returns the
// HTML/SVG strings the analysis view builds for each result read from stdin. No browser, no network.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = process.argv[2];
const el = () => ({ style: {}, addEventListener() {}, set innerHTML(v) {}, value: "0" });
const ctx = { document: { getElementById: () => el() }, window: { addEventListener() {} }, location: { protocol: "file:" } };
vm.createContext(ctx);
for (const f of ["ui.js", "analysis_model.js", "analysis_timeline.js", "analysis_view.js"])
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
ctx.results = JSON.parse(fs.readFileSync(0, "utf8"));
const out = vm.runInContext(`results.map((d) => {
  const m = buildModel(d);
  return [statsHtml(m), explicitHtml(m), tempoHtml(m), signalHtml(m), alignmentHtml(m),
    sectionsHtml(m), eventsHtml(m), timelineSvg(m, 0, m.duration, 900),
    timelineSvg(m, 10, 30, 900), timelineLegend(m)].join("\\n");
})`, ctx);
process.stdout.write(JSON.stringify(out));
