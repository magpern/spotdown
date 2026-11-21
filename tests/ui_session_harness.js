// Test helper (tests/test_ui_session.py): runs ui.js in a stubbed DOM with a recording fetch and
// prints every request the page makes and where it navigates. No browser, no network.
const vm = require("vm"), fs = require("fs"), path = require("path");
const [dir, status] = [process.argv[2], Number(process.argv[3])];
const els = {};
const el = (id) => els[id] || (els[id] = {
  id, style: {}, disabled: false, addEventListener() {}, innerHTML: "", className: "",
  value: id === "url" ? "https://open.spotify.com/track/abc" : id === "bitrate" ? "320" : "mp3",
});
const calls = [], navigations = [];
const ctx = {
  document: { getElementById: el }, window: { addEventListener() {} },
  location: { protocol: "http:", assign: (u) => navigations.push(u) },
  setTimeout: (f) => f(), JSON, Object, Number, String, Promise, Error, encodeURIComponent,
  fetch: async (url, opts) => {
    calls.push({ url, opts });
    const body = url.includes("/tasks/") ? { status: "done", download_url: "/files/x.mp3" }
      : { tracks: [{ id: "t1", title: "T" }], count: 1, status: "queued", task_id: "j" };
    return { status, ok: status < 300, json: async () => body };
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(dir, "ui.js"), "utf8"), ctx, { filename: "ui.js" });
vm.runInContext("currentTrackId = 't1'; var renderAnalysis = () => {};", ctx);
(async () => {
  for (const fn of ["lookup", "convert", "analyze"]) await vm.runInContext(`${fn}()`, ctx);
  process.stdout.write(JSON.stringify({ calls, navigations }));
})();
