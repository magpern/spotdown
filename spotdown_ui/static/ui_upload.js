"use strict";
// Local-file analysis (ADR 0002 in the UI image, ADR 0005): the file is the body of one
// PUT /api/uploads (application/octet-stream, no file name), which the UI server streams to the
// backend; then the analysis runs in the UI's scope. The waveform is drawn from the picked file
// itself, so no audio of a local file ever comes back.
const fileInput = $("file"), dropZone = $("drop");
let busy = false;
function setBusy(on) { busy = on; fileInput.disabled = on; dropZone.className = on ? "drop busy" : "drop"; }

async function analyzeFile(file) {
  if (!file || busy) return;
  setBusy(true);
  showStatus('<span class="spin"></span>Uploading the file…');
  try {
    const upload = await apiJson("/api/uploads", { method: "PUT", binary: file });
    const post = await apiJson("/api/analyses", { method: "POST", json: { upload_id: upload.upload_id } });
    const job = await waitForJob(post, "Analysing on the server…");
    await showAnalysis(job, () => file.arrayBuffer(), { local: true });
  } catch (e) {
    showStatus("Analysis failed: " + e.message, true);
  } finally { setBusy(false); }
}

fileInput.addEventListener("change", () => analyzeFile(fileInput.files && fileInput.files[0]));
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); if (!busy) dropZone.className = "drop over"; });
dropZone.addEventListener("dragleave", () => { if (!busy) dropZone.className = "drop"; });
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  if (busy) return;
  dropZone.className = "drop";
  analyzeFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});
