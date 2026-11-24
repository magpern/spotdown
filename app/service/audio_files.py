"""The kept mp3 files on disk (``data_dir/audio/<audio id>.mp3``) and their sweep.

A path is built only from an id that ``audio_policy.AUDIO_ID`` accepts, and that id is always one
the server made. A file is kept ``AUDIO_TTL_S`` from the last job that handed it out
(``audio_store``); the sweep deletes the expired ones, and any file in the directory without a row
once it is older than a job's lease (a worker killed between moving a file in and recording it).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..config import Settings
from . import audio_policy
from . import idempotency_policy as idem
from .dispatch import job_lease_s
from .store import Store

SUFFIX = ".mp3"


def audio_path(settings: Settings, audio_id: str) -> Path:
    if not audio_policy.is_audio_id(audio_id):
        raise ValueError("not an audio id")
    return settings.audio_dir / (audio_id + SUFFIX)


def purge_expired_audio(settings: Settings, store: Store, now: Optional[float] = None) -> int:
    """Deletes the expired files and their rows, then the files no row knows; returns how many
    files were deleted."""
    now = time.time() if now is None else now
    record_ttl = audio_policy.job_record_ttl_s(settings.audio_ttl_s, idem.WINDOW_S)
    deleted = 0
    for audio_id in store.purge_expired_audio(now, record_ttl):
        audio_path(settings, audio_id).unlink(missing_ok=True)
        deleted += 1
    if not settings.audio_dir.is_dir():
        return deleted
    entries = list(settings.audio_dir.iterdir())
    known = store.audio_ids()  # read after the listing: a file recorded meanwhile is known
    for entry in entries:
        if entry.name.endswith(SUFFIX) and entry.name[: -len(SUFFIX)] in known:
            continue
        try:
            fresh = entry.lstat().st_mtime > now - job_lease_s(settings)
        except FileNotFoundError:
            continue
        if not fresh and (entry.is_symlink() or not entry.is_dir()):
            entry.unlink(missing_ok=True)
            deleted += 1
    return deleted
