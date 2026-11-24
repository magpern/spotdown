// Test helper (tests/ui_app/test_ui_page.py): runs the UI page's scripts (index.html's list) in a
// stubbed DOM with a scripted fake fetch of the UI server's /api routes. Reads {document, title}
// on stdin, drives the link flow (look up, pick, analyse, make the mp3) and the local-file flow,
// and prints what the page sent and showed. No browser, no network.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const els = {}, handlers = {}, picks = {};
const el = (id) => els[id] || (els[id] = {
  id, style: {}, className: "", disabled: false, files: null, value: "320", checked: false,
  clientWidth: 900, html: "", textContent: "",
  addEventListener(ev, fn) { (handlers[id] = handlers[id] || {})[ev] = fn; },
  setAttribute() {}, getBoundingClientRect() { return { left: 0 }; },
  set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; },
  querySelectorAll() {
    return [...this.html.matchAll(/data-i="(\d+)"/g)].map((m) => ({
      getAttribute: () => m[1], addEventListener: (ev, fn) => { picks[m[1]] = fn; },
    }));
  },
});
const calls = [], navigations = [];
const picked = { name: "song.wav", size: 3, type: "audio/wav", arrayBuffer: async () => "BYTES" };
const ok = (status, body) => ({
  status, ok: status >= 200 && status < 300, json: async () => body, arrayBuffer: async () => "MP3",
});
const track = { spotify_id: "4uLU6hMCjMI75M1A2tKUQC", title: input.title, artists: [input.title],
  album: "A", duration_ms: 213000, isrc: "X" };
// What Spotify's public pages may give: no artists, album, duration or ISRC (#76).
const bare = { spotify_id: "1111111111111111111111", title: "Bare", artists: [], album: null, duration_ms: null, isrc: null };
let jobPolls = 0;
function respond(url, method) {
  if (url === "/api/spotify/resolve") return ok(200, { tracks: input.bare ? [bare] : [track], listed: 1, total: 250, truncated: true });
  if (url === "/api/match") return ok(200, { results: [{ status: "matched", best: {
    source: "youtube:dQw4w9WgXcQ", recording_match_confidence: 0.9, duration_check: "ok" } }] });
  if (url === "/api/uploads") return ok(201, { upload_id: "up_1", sha256: "a", bytes: 3 });
  if (url === "/api/analyses") return ok(202, { job_id: "job_1", status: "queued" });
  if (url === "/api/audio" && method === "POST") return ok(202, { job_id: "job_a" });
  if (url === "/api/jobs/job_1") {
    jobPolls++;
    return ok(200, jobPolls % 2 ? { job_id: "job_1", status: "running" } : { job_id: "job_1", status: "done", analysis_id: "an_1" });
  }
  if (url === "/api/audio-jobs/job_a") return ok(200, { job_id: "job_a", status: "done", audio_id: "au_1" });
  if (url === "/api/analyses/an_1") return ok(200, input.document);
  if (url === "/api/audio/au_1") return ok(200, {});
  return ok(404, { error: { code: "not_found", message: "not found" } });
}
const ctx = {
  document: { getElementById: el },
  window: { addEventListener() {} },  // no AudioContext: the waveform falls back to a message
  location: { origin: "http://localhost:8000", assign: (u) => navigations.push(u) },
  console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
  setTimeout: (f) => f(), requestAnimationFrame() {}, cancelAnimationFrame() {},
  setInterval() { return 1; }, clearInterval() {},
  fetch: async (url, opts) => {
    const o = opts || {};
    calls.push({ url, method: o.method || "GET", headers: o.headers || {}, credentials: o.credentials,
      mode: o.mode, body: o.body === picked ? "<picked file>" : o.body === undefined ? null : String(o.body) });
    return respond(url, o.method || "GET");
  },
};
vm.createContext(ctx);
const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
for (const f of [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]))
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
const snapshot = () => ({ status: els.status.html, analysis: els.analysisCard.html });
(async () => {
  const steps = {};
  el("url").value = "  https://open.spotify.com/playlist/x  ";
  await handlers.lookup.click(); await settle();
  steps.tracks = els.tracksCard.html;
  await picks["0"](); await settle();
  steps.match = els.matchInfo.html;
  await handlers.analyze.click(); await settle();
  steps.linkAnalysis = snapshot();
  el("bitrate").value = "192";
  await handlers.mp3.click(); await settle();
  steps.download = els.download.html;
  const before = calls.length;
  el("file").files = [picked];
  await handlers.file.change(); await settle();
  steps.fileAnalysis = snapshot();
  process.stdout.write(JSON.stringify({ calls, fileCalls: calls.slice(before), steps, navigations }));
})().catch((e) => { process.stdout.write(JSON.stringify({ error: String(e && e.stack || e) })); });
