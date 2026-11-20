"""The one module that drives yt-dlp: a flat search and the download of one video by its id.

Hardening: only the ``youtube`` (and, for search, ``youtube:search``) extractors are loaded, so a
crafted reference cannot reach another site; the download URL is built from a validated
11-character video id, never taken from a search entry; no playlists, no post-processors, a size
cap and a duration cap checked before any byte is fetched. yt-dlp's own messages go to a quiet
logger and are only classified (``error_policy``), never logged: they can quote URLs.

Tests inject a fake ``factory`` in place of ``yt_dlp.YoutubeDL``; nothing here is ever run against
YouTube by an agent or by CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..service.failure_policy import JobFailure, Reason
from . import error_policy
from . import normalise_policy as norm

Factory = Callable[[Dict[str, Any]], Any]
SOCKET_TIMEOUT_S = 30
_DOWNLOAD_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=mp3]"


def download_opts(outtmpl: str, fmt: str = "bestaudio/best") -> Dict[str, Any]:
    """The options both the standalone app and the service use to download one item."""
    return {
        "format": fmt,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": False,
        "retries": 3,
        # YouTube challenges the default "web" client ("page needs to be reloaded").
        # The android/tv clients skip that challenge without cookies or a PO token.
        # This is brittle by nature — YouTube changes it often (see README).
        "extractor_args": {"youtube": {"player_client": ["android", "tv", "web"]}},
    }


class _QuietLogger:
    """Swallows yt-dlp's output: its messages can carry URLs with query strings."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _hardened(opts: Dict[str, Any], extractors: List[str]) -> Dict[str, Any]:
    return {
        **opts,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "allowed_extractors": extractors,
        "socket_timeout": SOCKET_TIMEOUT_S,
        "cachedir": False,
        "noplaylist": True,
        "postprocessors": [],
    }


def _real_factory(opts: Dict[str, Any]) -> Any:
    import yt_dlp  # imported lazily so the API can boot without it

    return yt_dlp.YoutubeDL(opts)  # type: ignore[arg-type]


def _failure(exc: BaseException) -> JobFailure:
    reason, retry = error_policy.classify(str(exc))
    return JobFailure(reason, retry)


@dataclass(frozen=True)
class Downloaded:
    path: Path
    metadata_duration_s: float


class YtDlpClient:
    def __init__(self, factory: Optional[Factory] = None) -> None:
        self._factory = factory or _real_factory

    def search(self, query: str, limit: int) -> List[Any]:
        """The flat search entries for ``query`` (ids, titles, channels, durations); no formats
        are resolved and nothing is downloaded. Raises ``JobFailure``."""
        opts = _hardened({"skip_download": True, "extract_flat": "in_playlist"}, ["youtube:search"])
        limit = max(1, min(int(limit), 20))
        try:
            with self._factory(opts) as ydl:
                result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except JobFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types; all are classified
            raise _failure(exc) from None
        entries = (result or {}).get("entries") if isinstance(result, dict) else None
        return list(entries or [])[:limit]

    def download(
        self, video_id: str, dest_dir: Path, *, max_bytes: int, max_duration_s: float
    ) -> Downloaded:
        """Downloads one video's audio into ``dest_dir`` as ``source.<ext>``. Raises
        ``JobFailure``; a partial file is left for the caller's work-directory cleanup."""
        if not norm.VIDEO_ID.match(video_id):
            raise JobFailure(Reason.SOURCE_REFUSED)
        dest_dir.mkdir(parents=True, exist_ok=True)
        opts = _hardened(
            download_opts(str(dest_dir / "source.%(ext)s"), _DOWNLOAD_FORMAT), ["youtube"]
        )
        opts.update(max_filesize=max_bytes, overwrites=True)
        try:
            with self._factory(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=False
                )
                duration = _checked(info, video_id, max_bytes, max_duration_s)
                ydl.process_ie_result(info, download=True)
        except JobFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types; all are classified
            raise _failure(exc) from None
        produced = sorted(p for p in dest_dir.glob("source.*") if p.is_file())
        if len(produced) != 1:
            raise JobFailure(Reason.TOO_LARGE if not produced else Reason.PROVIDER_ERROR)
        if produced[0].stat().st_size > max_bytes:
            raise JobFailure(Reason.TOO_LARGE)
        return Downloaded(produced[0], duration)


def _checked(info: Any, video_id: str, max_bytes: int, max_duration_s: float) -> float:
    """The metadata checks made before any audio byte is fetched; the metadata duration."""
    if not isinstance(info, dict) or info.get("id") != video_id:
        raise JobFailure(Reason.SOURCE_MISSING)
    if info.get("live_status") in ("is_live", "is_upcoming", "post_live") or info.get("is_live"):
        raise JobFailure(Reason.SOURCE_MISSING)
    duration = info.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise JobFailure(Reason.SOURCE_MISSING)
    if duration > max_duration_s:
        raise JobFailure(Reason.TOO_LONG)
    size = info.get("filesize") or info.get("filesize_approx")
    if isinstance(size, (int, float)) and size > max_bytes:
        raise JobFailure(Reason.TOO_LARGE)
    return float(duration)
