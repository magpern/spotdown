// Local-file analysis (ADR 0002): a file picker and drop zone, shown only when the server offers
// the route. The file goes as application/octet-stream with X-Spotdown-UI and the session cookie,
// and no file name; the waveform is drawn from the picked file itself (the server never sends
// audio back). A 401 on the upload goes to /login.
const UPLOAD_PATH = "/api/v1/analyze-upload";
const fileCard = $("fileCard"), fileInput = $("file"), dropZone = $("drop");

function toLogin() { location.assign("/login"); }

async function uploadAvailable() {
  try {
    const r = await fetch(UPLOAD_PATH, { headers: { "X-Spotdown-UI": "1" }, credentials: "same-origin" });
    return r.ok;
  } catch (e) { return false; }
}

async function analyzeFile(file) {
  if (!file) return;
  fileInput.disabled = true;
  showStatus('<span class="spin"></span>Analyzing the file… (this can take a while)');
  try {
    const r = await fetch(UPLOAD_PATH, {
      method: "POST", credentials: "same-origin", body: file,
      headers: { "Content-Type": "application/octet-stream", "X-Spotdown-UI": "1" },
    });
    if (r.status === 401) return toLogin();
    const d = await r.json();
    if (!r.ok) return showStatus("Analysis failed: " + (d.detail || ("HTTP " + r.status)), true);
    // The waveform (spotdown#48) is decoded from the user's own file, never fetched from the server.
    renderAnalysis(d, () => file.arrayBuffer());
    showStatus("Analysis ready.");
  } catch (e) {
    showStatus("Analysis failed: " + e.message, true);
  } finally { fileInput.disabled = false; }
}

fileInput.addEventListener("change", () => analyzeFile(fileInput.files && fileInput.files[0]));
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.className = "drop over"; });
dropZone.addEventListener("dragleave", () => { dropZone.className = "drop"; });
dropZone.addEventListener("drop", (e) => {
  e.preventDefault(); dropZone.className = "drop";
  analyzeFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});
const uploadReady = uploadAvailable().then((ok) => { if (ok) fileCard.style.display = "block"; return ok; });
