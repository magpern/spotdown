"""The worker side of a v2 job: fetch the source, analyse it in the sandbox, store the result.

A job always ends ``done`` or ``failed`` and never raises into the worker, so one bad file never
takes the worker down. Its temporary audio (the upload, the fetched file, the sandbox's work
directory) is deleted at the end of every job, whether it succeeds or fails.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..config import Settings, get_settings
from . import analysis_doc, media_policy, sandbox, url_fetch, url_policy
from .failure_policy import JobFailure, Reason, outcome_for
from .store import Store
from .uploads import upload_path

log = logging.getLogger("spotdown.service.worker")

Fetcher = Callable[..., url_fetch.Fetched]
Analyser = Callable[[Path, str, Path, sandbox.Limits], Dict[str, Any]]


def limits_from(settings: Settings) -> sandbox.Limits:
    return sandbox.Limits(
        memory_bytes=settings.analysis_max_memory_mb * 1024 * 1024,
        cpu_s=settings.analysis_max_cpu_s,
        wall_s=float(settings.analysis_wall_timeout_s),
        decoded_s=settings.analysis_max_decoded_s,
    )


def _sniff_file(path: Path) -> Optional[str]:
    with path.open("rb") as f:
        return media_policy.sniff(f.read(media_policy.SNIFF_BYTES))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _source(
    job: Dict[str, Any], settings: Settings, store: Store, work: Path, fetcher: Fetcher
) -> tuple[Path, str, str, Dict[str, Any], Dict[str, float]]:
    """``(audio file, media, sha256, provenance source, timings)`` for the job's source."""
    if job["source_kind"] == "upload":
        upload = store.get_upload(job["upload_id"] or "")
        path = upload_path(settings, job["upload_id"] or "")
        if upload is None or not path.is_file():
            raise JobFailure(Reason.SOURCE_MISSING)
        return path, upload["media"], upload["sha256"], {"kind": "upload"}, {}
    if job["source_kind"] == "url" and job["fetch_url"]:
        t0 = time.monotonic()
        fetched = fetcher(
            job["fetch_url"],
            work / "source",
            allowed_hosts=url_policy.parse_allowlist(settings.url_allowed_hosts),
            max_bytes=settings.max_upload_bytes,
            deadline_s=settings.url_fetch_timeout_s,
        )
        media = _sniff_file(fetched.path)
        if media is None:
            raise JobFailure(Reason.UNSUPPORTED_MEDIA)
        source = {"kind": "url", "ref": fetched.ref}
        return fetched.path, media, fetched.sha256, source, {"fetch": time.monotonic() - t0}
    raise JobFailure(Reason.SOURCE_MISSING)


def execute_job(
    job_id: str,
    settings: Settings,
    store: Store,
    *,
    fetcher: Fetcher = url_fetch.fetch_url,
    analyser: Analyser = sandbox.run_analysis,
) -> None:
    job = store.get_job(job_id)
    if job is None or not store.start_job(job_id):
        return
    work = settings.work_dir / job_id
    try:
        work.mkdir(parents=True, exist_ok=True)
        audio, media, sha256, source, timings = _source(job, settings, store, work, fetcher)
        result = analyser(audio, media, work, limits_from(settings))
        analysis_id = "an_" + uuid.uuid4().hex
        doc = analysis_doc.build(
            core=result["core"],
            probe=result["probe"],
            components=result["components"],
            sha256=sha256,
            source=source,
            job_id=job_id,
            analysis_id=analysis_id,
            timings_s={**timings, **result["timings_s"]},
            created_at=_now(),
        )
        store.finish_job_done(job_id, analysis_id, job["cache_scope"], doc)
    except JobFailure as failure:
        outcome, kind = outcome_for(failure.reason)
        retry = failure.retry_after_s if outcome == "rate_limited" else None
        store.finish_job_failed(job_id, outcome, kind, retry)
        log.info("job %s failed: %s", job_id, failure.reason.value)
    except Exception as exc:  # noqa: BLE001 - one job never takes the worker down
        outcome, kind = outcome_for(Reason.ANALYSIS)
        store.finish_job_failed(job_id, outcome, kind, None)
        # Only the type: a message could quote a URL.
        log.error("job %s failed: %s", job_id, type(exc).__name__)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if job["upload_id"]:
            upload_path(settings, job["upload_id"]).unlink(missing_ok=True)
            store.delete_upload(job["upload_id"])
        store.clear_fetch_url(job_id)


def run_job(job_id: str) -> None:
    """The RQ entry point (``app.service.worker.run_job``)."""
    settings = get_settings()
    execute_job(job_id, settings, Store(settings.service_db_path))
