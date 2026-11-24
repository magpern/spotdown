"""The worker side of ``POST /v2/audio``: one matched YouTube video as an mp3 at the asked bitrate.

The job reuses a kept file of the same video and bitrate when there is one. Otherwise it downloads
through the ``youtube`` source (``youtube_source.fetch_youtube``: the ``ALLOW_YTDLP_SOURCING``
gate, the video id check, the provider backoff, one audio-only https format, the
``MAX_UPLOAD_BYTES`` cap and its failure kinds), converts in the sandbox (``transcode_child``),
writes the ID3 title and artists the caller gave (no cover art: nothing else is fetched), and only
then moves the finished file into ``audio_files``. Everything before that lives in the job's work
directory, deleted at the end of every job: a failed or oversized download leaves nothing that
could be served.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict

from ..config import Settings
from ..schemas import TrackMeta
from ..transcode import tag
from ..youtube.ytdlp_client import YtDlpClient
from . import audio_policy, sandbox
from . import sandbox_child as child
from .audio_files import audio_path
from .failure_policy import JobFailure, Reason, outcome_for
from .store import Store
from .youtube_source import fetch_youtube

log = logging.getLogger("spotdown.service.audio_job")

Transcoder = Callable[[Path, str, Path, int, sandbox.Limits], None]


def run_transcode(src: Path, media: str, dest: Path, bitrate: int, limits: sandbox.Limits) -> None:
    """Converts ``src`` to an mp3 at ``dest`` in the sandbox; JobFailure on any failure."""
    cmd = [
        sys.executable,
        "-E",
        "-s",
        "-m",
        "app.service.transcode_child",
        str(src),
        str(dest),
        media,
        str(int(bitrate)),
        repr(float(limits.decoded_s)),
        repr(float(limits.wall_s)),
    ]
    cpu_before = sandbox._children_cpu_s()
    returncode = sandbox.run_child(cmd, dest.parent, limits)
    if returncode != child.EXIT_OK:
        cpu_used = sandbox._children_cpu_s() - cpu_before
        raise JobFailure(sandbox._reason_for(returncode, cpu_used, limits))


def _tags(path: Path, request: Dict[str, Any]) -> None:
    """The ID3 title and artists, cleaned when the request was accepted. No ``cover_url``: the
    tagger fetches nothing."""
    meta = TrackMeta(id=request["video_id"], title=request["title"], artists=request["artists"])
    tag(path, "mp3", meta)


def execute_audio_job(
    job_id: str,
    settings: Settings,
    store: Store,
    youtube: YtDlpClient,
    *,
    limits: sandbox.Limits,
    transcoder: Transcoder = run_transcode,
    clock: Callable[[], float] = time.time,
) -> None:
    """Runs one queued audio job to ``done`` (with an ``audio_id``) or ``failed``; never raises."""
    if not store.start_job(job_id):
        return
    work = settings.work_dir / job_id
    try:
        request = store.audio_request(job_id)
        if request is None:
            raise JobFailure(Reason.SOURCE_MISSING)
        if not settings.allow_ytdlp_sourcing:
            raise JobFailure(Reason.SOURCE_REFUSED)  # switched off after the job was accepted
        video_id, bitrate = request["video_id"], int(request["bitrate"])
        if bitrate not in audio_policy.BITRATES:
            raise JobFailure(Reason.SOURCE_REFUSED)
        if store.finish_audio_reused(job_id, video_id, bitrate, clock(), settings.audio_ttl_s):
            return
        work.mkdir(parents=True, exist_ok=True)
        got = fetch_youtube(video_id, settings, store, work, youtube, clock)
        out = work / "out.mp3"
        transcoder(got.path, got.media, out, bitrate, limits)
        _tags(out, request)
        _keep(job_id, out, request, settings, store, clock)
    except JobFailure as failure:
        outcome, fkind = outcome_for(failure.reason)
        retry = failure.retry_after_s if outcome == "rate_limited" else None
        store.finish_job_failed(job_id, outcome, fkind, retry)
        log.info("audio job %s failed: %s", job_id, failure.reason.value)
    except Exception as exc:  # noqa: BLE001 - one job never takes the worker down
        outcome, fkind = outcome_for(Reason.ANALYSIS)
        store.finish_job_failed(job_id, outcome, fkind, None)
        log.error("audio job %s failed: %s", job_id, type(exc).__name__)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _keep(
    job_id: str,
    out: Path,
    request: Dict[str, Any],
    settings: Settings,
    store: Store,
    clock: Callable[[], float],
) -> None:
    """Moves the finished mp3 in and records it; the file is deleted again if that fails."""
    if not out.is_file() or out.stat().st_size == 0:
        raise JobFailure(Reason.DECODE)
    audio_id = audio_policy.new_audio_id()
    final = audio_path(settings, audio_id)
    name = audio_policy.file_name(request["title"], request["artists"], request["video_id"])
    record = {
        "audio_id": audio_id,
        "video_id": request["video_id"],
        "bitrate": int(request["bitrate"]),
        "file_name": name,
    }
    os.replace(out, final)
    try:
        kept = store.finish_audio_done(job_id, record, clock(), settings.audio_ttl_s)
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    if not kept:  # the job is no longer running (expired by the sweep): nothing is kept
        final.unlink(missing_ok=True)
