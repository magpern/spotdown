// Test helper (tests/test_ui_upload.py): runs ui.js and upload.js in a stubbed DOM with a fake
// fetch, picks a file, and prints what was sent and what the waveform hook received. No browser,
// no network.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = process.argv[2], status = Number(process.argv[3]), probeStatus = Number(process.argv[4] || 200);
const els = {};
const el = (id) => els[id] || (els[id] = {
  id, style: {}, handlers: {}, disabled: false, files: null, className: "",
  addEventListener(type, fn) { this.handlers[type] = fn; }, set innerHTML(v) { this.html = v; },
});
const calls = [], waveform = [], rendered = [];
const BYTES = { picked: true };  // what the picked file's arrayBuffer() resolves to
const picked = { name: "secret-name.mp3", size: 3, type: "audio/mpeg", arrayBuffer: async () => BYTES };
const ctx = {
  document: { getElementById: el }, window: {}, console,
  location: { protocol: "http:", assigned: null, assign(u) { this.assigned = u; } },
  fetch: async (url, opts) => {
    calls.push({ url, opts: opts || {} });
    const probe = !opts || !opts.method || opts.method === "GET";
    const code = probe ? probeStatus : status;
    return { ok: code === 200, status: code, json: async () => (code === 200 ? { probe, bpm: 120 } : { detail: "no" }) };
  },
};
vm.createContext(ctx);
for (const f of ["ui.js", "upload.js"]) vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
ctx.renderAnalysis = (d, loadAudio) => rendered.push({ d, loadAudio });
ctx.picked = picked;
(async () => {
  const shown = await vm.runInContext("uploadReady", ctx);
  els.file.files = [picked];
  els.file.handlers.change();
  await new Promise((r) => setTimeout(r, 50));
  const fetchesBefore = calls.length;
  for (const r of rendered) waveform.push({ same: r.loadAudio ? (await r.loadAudio()) === BYTES : false });
  const fetchedForAudio = calls.length - fetchesBefore;
  const post = calls.find((c) => c.opts.method === "POST");
  // The real renderAnalysis hands that loader to the waveform (everything else stubbed).
  const got = [], stubbed = ["statsHtml", "explicitHtml", "tempoHtml", "signalHtml", "alignmentHtml",
    "timelineControls", "timelineLegend", "sectionsHtml", "eventsHtml", "wireTimeline", "drawTimeline"];
  const view = { console, window: { addEventListener() {} }, analysisCard: { style: {} },
    buildModel: () => ({ prov: {} }), escapeHtml: String, loadTrackAudio: "default",
    startWaveform: (m, load) => got.push(load) };
  vm.createContext(view);
  vm.runInContext(fs.readFileSync(path.join(dir, "analysis_view.js"), "utf8"), view, { filename: "analysis_view.js" });
  vm.runInContext(stubbed.map((n) => `${n} = () => "";`).join(" "), view);
  const mine = () => 0;
  vm.runInContext("renderAnalysis", view)({}, mine);
  vm.runInContext("renderAnalysis", view)({});
  const viewPasses = got[0] === mine && got[1] === "default";
  process.stdout.write(JSON.stringify({ viewPasses,
    shown, card: els.fileCard.style.display, probe: calls[0],
    post: post && { url: post.url, method: post.opts.method, headers: post.opts.headers,
      credentials: post.opts.credentials, mode: post.opts.mode, bodyIsFile: post.opts.body === picked },
    waveform, fetchedForAudio, rendered: rendered.length, login: ctx.location.assigned,
  }));
})();
