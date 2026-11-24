"""One audio file as one real service job (sandboxed child, throwaway store), measured.

The file is *copied* into the throwaway upload store, never moved or modified, and the copy is
deleted with the store, so the owner's audio stays where it is and nothing is left behind (the
``audio_kept_after_job`` gate counts what is). Run as a module it executes one job in its own
process and writes the result as JSON, so that ``RUSAGE_CHILDREN`` of that process is the peak
memory of that one track's analysis child (``peak_gib``), which a shared worker pool cannot give.

    python -m app.quality.track_job <audio> <result.json> <track-id> [reference-json]

Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..service import media_policy
from ..service.store import Store
from ..service.uploads import upload_path
from ..service.worker import execute_job

SCOPE = "quality"
MEMORY_LIMIT_MB = 1536  # the 1.5 GiB gate, enforced on the analysis child as well as measured


def make_settings(data_dir: Path) -> Settings:
    key = data_dir / "service_key"
    data_dir.mkdir(parents=True, exist_ok=True)
    key.write_text("quality-harness-key-" + "0" * 32 + "\n", encoding="utf-8")
    return Settings(
        service_key_file=key,
        data_dir=data_dir / "data",
        redis_url="redis://127.0.0.1:1/0",
        allow_ytdlp_sourcing=False,  # the harness analyses uploads only
        analysis_max_memory_mb=MEMORY_LIMIT_MB,
        analysis_max_cpu_s=240,
        analysis_wall_timeout_s=300,
    )


def files_under(*roots: Path) -> List[Path]:
    return [p for r in roots if r.exists() for p in r.rglob("*") if p.is_file()]


def sniff_media(audio: Path) -> str:
    """The container the service accepts (mp3, mp4, wav) or ValueError: the real path would
    refuse any other, so a run must not pretend to analyse it."""
    with audio.open("rb") as fh:
        media = media_policy.sniff(fh.read(media_policy.SNIFF_BYTES))
    if media is None:
        raise ValueError(f"{audio.name}: only mp3, mp4 and wav are accepted (convert it first)")
    return media


def run_audio_job(
    track_id: str, audio: Path, reference: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """One file as one job; a failed job is a result (``ok`` False), a bad file raises."""
    from ..service.routes import job_view

    media = sniff_media(audio)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="quality-") as tmp:
        root = Path(tmp)
        settings = make_settings(root)
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.service_db_path)
        job_id = "job_" + hashlib.sha256(track_id.encode()).hexdigest()[:24]
        upload_id = "up_" + job_id
        digest = hashlib.sha256()
        size = 0
        with audio.open("rb") as src, upload_path(settings, upload_id).open("wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
                dst.write(chunk)
        store.add_upload(upload_id, SCOPE, digest.hexdigest(), size, media, 3600)
        store.claim_upload(upload_id, SCOPE)
        job: Dict[str, Any] = {
            "job_id": job_id,
            "cache_scope": SCOPE,
            "source_kind": "upload",
            "upload_id": upload_id,
        }
        if reference:
            job["reference"] = reference
        store.add_job(job)
        execute_job(job_id, settings, store)
        row = store.get_job(job_id)
        view = job_view(row) if row else {"status": "missing"}
        doc = None
        if view.get("status") == "done":
            # a file-clock analysis, or an aligned one (its own id, composed on read)
            doc = store.get_analysis(view["analysis_id"]) or store.get_aligned_in_scope(
                view["analysis_id"], SCOPE
            )
        left = len(files_under(settings.uploads_dir, settings.work_dir))
    timings = (doc or {}).get("provenance", {}).get("run", {}).get("timings_s", {})
    return {
        "id": track_id,
        "ok": doc is not None,
        "outcome": view.get("outcome"),
        "doc": doc,
        "audio_left": left,
        "duration_s": (doc or {}).get("signal", {}).get("duration_s"),
        "work_s": float(timings.get("decode", 0.0)) + float(timings.get("analyse", 0.0)),
        "wall_s": time.monotonic() - t0,
    }


def peak_children_gib() -> float:
    """Peak resident memory of this process's waited-for children (ru_maxrss is KiB on Linux)."""
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0 / 1024.0


def main(argv: List[str]) -> int:
    audio, out, track_id = Path(argv[0]), Path(argv[1]), argv[2]
    reference = json.loads(argv[3]) if len(argv) > 3 else None
    result = run_audio_job(track_id, audio, reference)
    result["peak_gib"] = peak_children_gib()
    out.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
