"""Recovery after a worker was killed mid-job (out of memory, a redeploy): its ``finally`` never
ran, so the job stayed ``running``, its ``fetch_url`` stayed set and its work directory (fetched
audio) stayed on disk.

A job ``running`` for longer than its lease (``dispatch.job_lease_s``: RQ's job timeout plus a
margin) is failed as ``processing_failed`` / ``timeout`` (the caller may retry a timeout once), its
``fetch_url`` is cleared and its upload removed. Work directories of jobs that are not running are
removed. The global analyses unused for the retention period or of an old analyzer version are
purged (``retention_policy``). The worker sweeps before each job; ``GET /v2/jobs/{id}`` expires
a stale job it is asked about, so a poller is answered even when no other job runs.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Optional

from ..config import Settings
from .analysis_doc import ANALYZER_VERSION_V2
from .dispatch import job_lease_s
from .failure_policy import Reason, outcome_for
from .store import Store
from .uploads import upload_path

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


def sweep(settings: Settings, store: Store, now: Optional[float] = None) -> None:
    now = now or time.time()
    expire_stale_jobs(settings, store, now)
    remove_orphan_work_dirs(settings, store)
    store.purge_global_references(now)
    store.purge_unused_analyses(now, ANALYZER_VERSION_V2, settings.global_analysis_ttl_s)
