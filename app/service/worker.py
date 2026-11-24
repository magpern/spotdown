"""The worker side of a v2 job: fetch the source, analyse it in the sandbox, store the result.

A job always ends ``done`` or ``failed`` and never raises into the worker, so one bad file never
takes the worker down. Its temporary audio (the upload, the fetched file, the sandbox's work
directory) is deleted at the end of every job, whether it succeeds or fails. Before each job the
worker sweeps what a killed worker left behind (``sweep``).
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from ..config import Settings, get_settings
from ..youtube import match_calc
from ..youtube.ytdlp_client import YtDlpClient
from . import (
    analysis_doc,
    cache_policy,
    explicit_calc,
    media_policy,
    sandbox,
    sweep,
    url_fetch,
    url_policy,
)
from .alignment_store import Aligning, aligning_for
from .audio_store import AUDIO_KIND
from .failure_policy import JobFailure, Reason, outcome_for
from .store import Store
from .uploads import upload_path
from .youtube_source import fetch_youtube

log = logging.getLogger("spotdown.service.worker")
# httpx's request-line logging (with the query) is pinned off in ``url_fetch``.

Fetcher = Callable[..., url_fetch.Fetched]
Analyser = Callable[[Path, str, Path, sandbox.Limits], Dict[str, Any]]


def limits_from(settings: Settings) -> sandbox.Limits:
    return sandbox.Limits(
        memory_bytes=settings.analysis_max_memory_mb * 1024 * 1024,
        cpu_s=settings.analysis_max_cpu_s,
        wall_s=float(settings.analysis_wall_timeout_s),
        decoded_s=settings.analysis_max_decoded_s,
        decode_s=settings.analysis_max_decode_s,
    )


def _sniff_file(path: Path) -> Optional[str]:
    with path.open("rb") as f:
        return media_policy.sniff(f.read(media_policy.SNIFF_BYTES))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Sourced:
    path: Path
    media: str
    sha256: str
    source: Dict[str, Any]
    timings: Dict[str, float]
    expected: Optional[Tuple[float, str]] = None  # for the decoded duration check (youtube)
    age_limit: Optional[int] = None  # explicit-signal evidence (youtube)
    match_explicit: Optional[bool] = None


def _source(
    job: Dict[str, Any],
    settings: Settings,
    store: Store,
    work: Path,
    fetcher: Fetcher,
    youtube: YtDlpClient,
) -> Sourced:
    """The job's source as an audio file with its media type, sha256 and provenance source."""
    if job["source_kind"] == "upload":
        upload = store.get_upload(job["upload_id"] or "")
        path = upload_path(settings, job["upload_id"] or "")
        if upload is None or not path.is_file():
            raise JobFailure(Reason.SOURCE_MISSING)
        return Sourced(path, upload["media"], upload["sha256"], {"kind": "upload"}, {})
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
        return Sourced(
            fetched.path, media, fetched.sha256, source, {"fetch": time.monotonic() - t0}
        )
    if job["source_kind"] == "youtube":
        got = fetch_youtube(job["source_ref"], settings, store, work, youtube)
        timings = {"fetch": got.fetch_s}
        return Sourced(
            got.path,
            got.media,
            got.sha256,
            got.source,
            timings,
            got.expected,
            got.age_limit,
            got.match_explicit,
        )
    raise JobFailure(Reason.SOURCE_MISSING)


def _check_decoded(sourced: Sourced, core: Dict[str, Any]) -> None:
    """The second duration check (classbeat plan §2), on the measured durations. It is recorded
    in provenance; a rejected one means the audio is not the matched recording."""
    if sourced.expected is None:
        return
    content = core["content_end_s"] - core["content_start_s"]
    check = match_calc.decoded_duration_check(
        sourced.expected[0], sourced.expected[1], core["duration_s"], content
    )
    if check["result"] == "rejected":
        raise JobFailure(Reason.RECORDING_MISMATCH)
    sourced.source["decoded_duration_check"] = check


def _check_reused(sourced: Sourced, doc: Dict[str, Any]) -> None:
    """The same check for a reused analysis of equal bytes (another video id may expect another
    duration), on the reused analysis' measured ``signal``. Nothing is recorded: the reused
    analysis is not changed."""
    if sourced.expected is None:
        return
    signal = doc["signal"]
    core = {
        "duration_s": signal["duration_s"],
        "content_start_s": signal["content_start_s"],
        "content_end_s": signal["content_end_s"],
    }
    _check_decoded(replace(sourced, source={}), core)


def _aligning(
    job: Dict[str, Any], base: Dict[str, Any], scope: Optional[str]
) -> Optional[Aligning]:
    """The alignment layer, when the job asked for it: on the file-clock analysis, in its scope."""
    reference = job.get("reference")
    return aligning_for(base, scope, reference) if reference else None


def execute_job(
    job_id: str,
    settings: Settings,
    store: Store,
    *,
    fetcher: Fetcher = url_fetch.fetch_url,
    analyser: Analyser = sandbox.run_analysis,
    youtube: Optional[YtDlpClient] = None,
) -> None:
    try:
        sweep.sweep(settings, store)
    except Exception as exc:  # noqa: BLE001 - recovery never blocks the job itself
        log.error("sweep failed: %s", type(exc).__name__)
    job = store.get_job(job_id)
    if job is not None and job["source_kind"] == AUDIO_KIND:  # POST /v2/audio: an mp3, no analysis
        from .audio_job import execute_audio_job

        lim = limits_from(settings)
        return execute_audio_job(job_id, settings, store, youtube or YtDlpClient(), limits=lim)
    if job is None or not store.start_job(job_id):
        return
    work = settings.work_dir / job_id
    try:
        work.mkdir(parents=True, exist_ok=True)
        sourced = _source(job, settings, store, work, fetcher, youtube or YtDlpClient())
        kind, version = sourced.source["kind"], analysis_doc.ANALYZER_VERSION_V2
        # A youtube analysis is global (cache_policy): stored without a scope and shared.
        is_global = cache_policy.is_global_kind(kind)
        scope = None if is_global else job["cache_scope"]
        if is_global:
            known = store.find_global_by_sha(sourced.sha256, version)
        else:
            known = store.find_by_sha(scope, sourced.sha256, version) if scope else None
        if known is not None:  # the same bytes were analysed already: reuse, don't analyse
            base = store.get_analysis(known.analysis_id)
            if base is None:  # deleted with its scope meanwhile, and this job with it
                raise JobFailure(Reason.SOURCE_MISSING)
            _check_reused(sourced, base)
            aligning = _aligning(job, base, scope)
            if not store.finish_job_reused(
                job_id, known.analysis_id, scope, kind, sourced.source.get("ref"), aligning
            ):  # purged (or deleted with its scope, and this job with it) meanwhile
                raise JobFailure(Reason.SOURCE_MISSING)
            return
        result = analyser(sourced.path, sourced.media, work, limits_from(settings))
        _check_decoded(sourced, result["core"])
        analysis_id = "an_" + uuid.uuid4().hex
        # A global analysis must not reveal who asked first: its run record carries an opaque
        # run id instead of the job id, and no timings (docs/contract-v2.md, "Provenance").
        run_id = "run_" + uuid.uuid4().hex if is_global else job_id
        timings = {} if is_global else {**sourced.timings, **result["timings_s"]}
        doc = analysis_doc.build(
            core=result["core"],
            probe=result["probe"],
            components=result["components"],
            sha256=sourced.sha256,
            source=sourced.source,
            job_id=run_id,
            analysis_id=analysis_id,
            timings_s=timings,
            created_at=_now(),
            content_evidence=explicit_calc.gather(
                caller=job.get("explicit"),
                caller_match=sourced.match_explicit,
                age_limit=sourced.age_limit,
            ),
        )
        store.finish_job_done(job_id, analysis_id, scope, doc, _aligning(job, doc, scope))
    except JobFailure as failure:
        outcome, fkind = outcome_for(failure.reason)
        retry = failure.retry_after_s if outcome == "rate_limited" else None
        store.finish_job_failed(job_id, outcome, fkind, retry)
        log.info("job %s failed: %s", job_id, failure.reason.value)
    except Exception as exc:  # noqa: BLE001 - one job never takes the worker down
        outcome, fkind = outcome_for(Reason.ANALYSIS)
        store.finish_job_failed(job_id, outcome, fkind, None)
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
    execute_job(job_id, settings, Store(settings.service_db_path, settings.global_reference_ttl_s))
