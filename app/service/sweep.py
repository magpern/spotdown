"""Recovery after a worker was killed mid-job (out of memory, a redeploy): its ``finally`` never
ran, so the job stayed ``running``, its ``fetch_url`` stayed set and its work directory (fetched
audio) stayed on disk.

A job ``running`` for longer than its lease (``dispatch.job_lease_s``: RQ's job timeout plus a
margin) is failed as ``processing_failed`` / ``timeout`` (the caller may retry a timeout once), its
``fetch_url`` is cleared and its upload removed. Work directories of jobs that are not running are
removed. The global and the scoped analyses unused for their retention period or of an old
analyzer version are purged (``retention_policy``), with the scoped job records not changed for
that period, the uploads past their TTL (``uploads``) and the mp3 files of ``POST /v2/audio``
past theirs (``audio_files``). The worker sweeps before each job;
``GET /v2/jobs/{id}`` expires a stale job it is asked about, so a poller is answered even when no
other job runs. Each step runs on its own: one that fails is logged and the others still run,
so a failing purge never keeps audio or another scope's expired entries.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Callable, List, Optional, Tuple

from ..config import Settings
from .analysis_doc import ANALYZER_VERSION_V2
from .audio_files import purge_expired_audio
from .dispatch import job_lease_s
from .failure_policy import Reason, outcome_for
from .store import Store
from .uploads import purge_expired_uploads, upload_path

log = logging.getLogger("spotdown.service.sweep")


def expire_stale_jobs(settings: Settings, store: Store, now: Optional[float] = None) -> int:
    """Fails the jobs past their lease; returns how many."""
    outcome, kind = outcome_for(Reason.DEADLINE)
    expired = store.expire_running(now or time.time(), job_lease_s(settings), outcome, kind)
    for job_id, upload_id in expired:
        if upload_id:
            upload_path(settings, upload_id).unlink(missing_ok=True)
            store.delete_upload(upload_id)
        log.info("job %s expired: its worker did not finish it", job_id)
    return len(expired)


def remove_orphan_work_dirs(settings: Settings, store: Store) -> None:
    """Removes the work directory of every job that is not running. The directories are listed
    before the running jobs are read, so a job that starts meanwhile (it is marked running
    before it creates its directory) never loses its directory."""
    if not settings.work_dir.is_dir():
        return
    entries = list(settings.work_dir.iterdir())
    running = store.running_job_ids()
    for entry in entries:
        if entry.name in running:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def _steps(settings: Settings, store: Store, now: float) -> List[Tuple[str, Callable[[], object]]]:
    """The sweep's steps, in order. Audio goes first (nothing after it can keep it)."""
    version = ANALYZER_VERSION_V2
    return [
        ("expire_stale_jobs", lambda: expire_stale_jobs(settings, store, now)),
        ("remove_orphan_work_dirs", lambda: remove_orphan_work_dirs(settings, store)),
        ("purge_expired_uploads", lambda: purge_expired_uploads(settings, store, now)),
        ("purge_expired_audio", lambda: purge_expired_audio(settings, store, now)),
        ("purge_global_references", lambda: store.purge_global_references(now)),
        (
            "purge_unused_analyses",
            lambda: store.purge_unused_analyses(now, version, settings.global_analysis_ttl_s),
        ),
        (
            "purge_scoped",
            lambda: store.purge_scoped(now, version, settings.scoped_retention_ttl_s),
        ),
    ]


def sweep(settings: Settings, store: Store, now: Optional[float] = None) -> List[str]:
    """Runs every step, each on its own: one that raises is logged (its exception type only,
    never its message, which could carry a URL) and the others still run. Returns the names of
    the steps that failed."""
    failed: List[str] = []
    for name, step in _steps(settings, store, now or time.time()):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - one failing step never skips the others
            log.error("sweep step %s failed: %s", name, type(exc).__name__)
            failed.append(name)
    return failed
