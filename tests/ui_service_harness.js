// Test helper (tests/test_ui_service.py): runs the service page's scripts (service.html's list) in
// a stubbed DOM with a scripted fake fetch and traps on every place a key could be kept or shown
// (storage, cookies, the URL, history, the console). Reads {scenario, key, document, error} on
// stdin and prints what the page sent, showed and stored. No browser, no network.
const vm = require("vm"), fs = require("fs"), path = require("path"), nodeCrypto = require("crypto");
const dir = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const els = {}, handlers = {};
const el = (id) => els[id] || (els[id] = {
  id, style: {}, className: "", disabled: false, files: null, value: "0", checked: false,
  clientWidth: 900, html: "", textContent: "",
  addEventListener(ev, fn) { (handlers[id] = handlers[id] || {})[ev] = fn; },
  setAttribute() {}, getBoundingClientRect() { return { left: 0 }; },
  set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; },
});
const traps = { storage: [], cookies: [], navigations: [], history: [], logs: [] };
const storage = (name) => ({
  setItem: (k, v) => traps.storage.push([name, k, v]), getItem: () => null, removeItem() {}, clear() {},
});
const record = (kind) => (...a) => traps.logs.push([kind, a.map(String).join(" ")]);
const calls = [];
const picked = { name: "song.mp3", size: 3, type: "audio/mpeg", arrayBuffer: async () => "BYTES" };
const ok = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
function respond(url, opts) {
  const s = input.scenario, method = (opts && opts.method) || "GET";
  if (s === "reject") return ok(401, { error: { code: "unauthorized", message: "unauthorized" } });
  if (url.startsWith("/v2/jobs/job_keycheck")) return ok(404, { error: { code: "not_found", message: "not found" } });
  if (method === "PUT") {
    if (s === "upload_error") return ok(415, { error: { code: "unsupported_media", message: input.error } });
    return ok(201, { upload_id: "up_1", sha256: "a".repeat(64), bytes: 3 });
  }
  if (method === "POST") return ok(202, { job_id: "job_1", status: "queued" });
  if (url.startsWith("/v2/jobs/job_1")) {
    const n = calls.filter((c) => c.url.startsWith("/v2/jobs/job_1")).length;
    if (s === "failed") return ok(200, { job_id: "job_1", status: "failed", outcome: "processing_failed", failure_kind: input.error });
    return ok(200, n < 2 ? { job_id: "job_1", status: "running" } : { job_id: "job_1", status: "done", analysis_id: "an_1" });
  }
  if (url.startsWith("/v2/analyses/an_1")) return ok(200, input.document);
  return ok(404, { error: { code: "not_found", message: "not found" } });
}
const ctx = {
  document: { getElementById: el, set cookie(v) { traps.cookies.push(v); }, get cookie() { return ""; } },
  window: { addEventListener() {} },  // no AudioContext: the waveform falls back to a message
  location: { protocol: "http:", origin: "http://svc.local", href: "http://svc.local/",
    assign: (u) => traps.navigations.push(u), replace: (u) => traps.navigations.push(u) },
  history: { pushState: (...a) => traps.history.push(a), replaceState: (...a) => traps.history.push(a) },
  localStorage: storage("local"), sessionStorage: storage("session"),
  console: { log: record("log"), warn: record("warn"), error: record("error"), info: record("info"), debug: record("debug") },
  crypto: nodeCrypto.webcrypto, setTimeout: (f) => f(), requestAnimationFrame() {}, cancelAnimationFrame() {},
  setInterval() { return 1; }, clearInterval() {},
  fetch: async (url, opts) => {
    const o = opts || {};
    calls.push({ url, method: o.method || "GET", headers: o.headers || {}, credentials: o.credentials,
      mode: o.mode, referrerPolicy: o.referrerPolicy,
      body: o.body === picked ? "<picked file>" : o.body === undefined ? null : String(o.body) });
    return respond(url, o);
  },
};
vm.createContext(ctx);
const html = fs.readFileSync(path.join(dir, "service.html"), "utf8");
for (const f of [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]))
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, { filename: f });
const settle = () => new Promise((r) => setImmediate(r));
(async () => {
  const states = [els.keyState.textContent];
  if (input.scenario !== "nokey") {
    el("key").value = "  " + input.key + "  ";
    await handlers.useKey.click();
    states.push(els.keyState.textContent);
  }
  const keyInputAfter = el("key").value;
  const before = calls.length;
  el("file").files = [picked];
  await handlers.file.change();
  for (let i = 0; i < 5; i++) await settle();
  states.push(els.keyState.textContent);
  const shown = Object.values(els).map((e) => [e.id, e.html, e.textContent, String(e.value)]);
  process.stdout.write(JSON.stringify({
    calls, fileCalls: calls.length - before, states, keyInputAfter, traps, shown,
    status: els.status.html, analysis: els.analysisCard ? els.analysisCard.html : "",
    keyVar: vm.runInContext("serviceKey", ctx),
  }));
})();
