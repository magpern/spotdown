"""The worker side of the ``youtube`` source: download one video's audio by its id.

The id was validated when the job was accepted and is checked again here. A provider backoff set
by a rate limit (from a match or an earlier download) fails the job ``rate_limited`` at once
without calling yt-dlp. The match record that chose this video, when there is one, goes into the
analysis' provenance; it is global data (public metadata and search results) and holds no scope,
``client_ref`` or job id. The video's age limit and the explicit flag of that match are evidence for
``content.explicit`` (``explicit_calc``), reported only.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from ..config import Settings
from ..youtube import error_policy
from ..youtube import normalise_policy as norm
from ..youtube.ytdlp_client import YtDlpClient
from . import match_policy, media_policy
from .failure_policy import JobFailure, Reason
from .store import Store


@dataclass(frozen=True)
class YoutubeAudio:
    path: Path
    media: str
    sha256: str
    source: Dict[str, Any]
    fetch_s: float
    expected: Tuple[float, str]  # (duration, "match" or "metadata") for the decoded check
    age_limit: Optional[int] = None
    match_explicit: Optional[bool] = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_youtube(
    video_id: Optional[str],
    settings: Settings,
    store: Store,
    work: Path,
    client: YtDlpClient,
    clock: Callable[[], float] = time.time,
) -> YoutubeAudio:
    if not settings.allow_ytdlp_sourcing:
        raise JobFailure(Reason.SOURCE_REFUSED)  # switched off after the job was accepted
    if not video_id or not norm.VIDEO_ID.fullmatch(video_id):
        raise JobFailure(Reason.SOURCE_REFUSED)
    wait = match_policy.retry_after(store.backoff_until(match_policy.PROVIDER), clock())
    if wait is not None:
        raise JobFailure(Reason.RATE_LIMITED, wait)
    t0 = time.monotonic()
    try:
        got = client.download(
            video_id,
            work / "youtube",
            max_bytes=settings.max_upload_bytes,
            max_duration_s=settings.analysis_max_decoded_s,
        )
    except JobFailure as failure:
        if failure.reason is Reason.RATE_LIMITED:
            retry = failure.retry_after_s or error_policy.DEFAULT_RETRY_AFTER_S
            store.set_backoff(match_policy.PROVIDER, clock() + retry)
            raise JobFailure(Reason.RATE_LIMITED, retry) from None
        raise
    with got.path.open("rb") as f:
        media = media_policy.sniff(f.read(media_policy.SNIFF_BYTES))
    if media is None:
        raise JobFailure(Reason.UNSUPPORTED_MEDIA)
    source: Dict[str, Any] = {"kind": "youtube", "ref": video_id}
    now = clock()
    choice = store.get_choice(video_id, now)
    match_explicit = None
    if choice is not None:
        source["match"], expected, match_explicit = choice[0], (choice[1], "match"), choice[2]
    else:
        expected = (got.metadata_duration_s, "metadata")
    fetch_s = time.monotonic() - t0
    return YoutubeAudio(
        got.path, media, _sha256(got.path), source, fetch_s, expected, got.age_limit, match_explicit
    )
