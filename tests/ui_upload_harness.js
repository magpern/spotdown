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
const picked = { name: "secret-name.mp3", size: 3, type: "audio/mpeg" };
const ctx = {
  document: { getElementById: el }, window: {}, console,
  location: { protocol: "http:", assigned: null, assign(u) { this.assigned = u; } },
  fetch: async (url, opts) => {
    calls.push({ url, opts: opts || {} });
    const probe = !opts || !opts.method;
    const code = probe ? probeStatus : status;
    return { ok: code === 200, status: code, json: async () => (code === 200 ? { probe, bpm: 120 } : { detail: "no" }) };
  },
};
vm.createContext(ctx);
for (const f of ["ui.js", "upload.js"]) vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
ctx.renderAnalysis = (d) => rendered.push(d);
ctx.drawWaveform = (file, d) => waveform.push({ same: file === picked, d });
ctx.picked = picked;
(async () => {
  const shown = await vm.runInContext("uploadReady", ctx);
  els.file.files = [picked];
  els.file.handlers.change();
  await new Promise((r) => setTimeout(r, 50));
  const post = calls.find((c) => c.opts.method === "POST");
  process.stdout.write(JSON.stringify({
    shown, card: els.fileCard.style.display, probe: calls[0],
    post: post && { url: post.url, method: post.opts.method, headers: post.opts.headers,
      credentials: post.opts.credentials, bodyIsFile: post.opts.body === picked },
    waveform, rendered: rendered.length, login: ctx.location.assigned,
  }));
})();
