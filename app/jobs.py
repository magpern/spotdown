from __future__ import annotations

import uuid
from pathlib import Path

from . import db, sources, storage, transcode
from .config import get_settings
from .schemas import TrackMeta


def run_pipeline(meta_dict: dict, source_url: str, bitrate: int, fmt: str) -> str:
    """Worker job: source -> transcode -> tag -> store -> cache. Returns download_url.

    Exactly one sourcing path is taken:
      * source_url set  -> download that explicit URL (operator-supplied, lawful-use path)
      * else            -> search for a match to the Spotify metadata (GATED)
    """
    settings = get_settings()
    settings.ensure_dirs()
    meta = TrackMeta(**meta_dict)
    vid = db.variant_id(meta.id, fmt, bitrate)

    # Serve from cache if a prior job already produced this exact variant.
    cached = db.get_done(vid)
    if cached:
        return storage.public_url(cached["file_name"])

    work = settings.tmp_dir / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        if source_url:
            raw = sources.download_direct(source_url, work, "src")
        else:
            raw = sources.source_from_spotify(meta, work, "src")

        out = work / f"out.{fmt}"
        transcode.transcode(raw, out, fmt, bitrate)
        transcode.tag(out, fmt, meta)

        file_name = storage.publish(out, meta.id, bitrate, fmt)
        db.mark_done(vid, meta.id, meta.title, meta.artist, file_name, bitrate, fmt)
        return storage.public_url(file_name)
    finally:
        # Clean up the per-job temp dir.
        for p in sorted(work.glob("**/*"), reverse=True):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
