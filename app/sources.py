from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .config import get_settings
from .schemas import TrackMeta


class SourceError(RuntimeError):
    pass


def _ydl_opts(outtmpl: str) -> dict:
    return {
        "format": "bestaudio/best",
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


def download_direct(source_url: str, dest_dir: Path, basename: str) -> Path:
    """Download a direct/explicit URL the operator supplies (lawful-use path)."""
    import yt_dlp  # imported lazily so the API can boot without it

    outtmpl = str(dest_dir / (basename + ".%(ext)s"))
    with yt_dlp.YoutubeDL(_ydl_opts(outtmpl)) as ydl:
        info = ydl.extract_info(source_url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        # yt-dlp may have remuxed; find the produced file
        produced = list(dest_dir.glob(basename + ".*"))
        if not produced:
            raise SourceError("Download produced no file")
        path = produced[0]
    return path


def _best_match(entries: list, meta: TrackMeta) -> Optional[dict]:
    """Prefer the closest duration match within tolerance; else the top-ranked result."""
    settings = get_settings()
    entries = [e for e in entries if e]
    if not entries:
        return None
    if meta.duration_s:
        best = None
        best_delta = None
        for e in entries:
            dur = e.get("duration")
            if not dur:
                continue
            delta = abs(dur - meta.duration_s)
            if best_delta is None or delta < best_delta:
                best, best_delta = e, delta
        if best is not None and best_delta <= settings.max_duration_drift_s:
            return best
    # No usable duration, or nothing within tolerance -> take YouTube's top hit.
    return entries[0]


def source_from_spotify(meta: TrackMeta, dest_dir: Path, basename: str) -> Path:
    """Find a matching audio source for a Spotify track and download it.

    GATED: disabled unless ALLOW_YTDLP_SOURCING=true. See README for why.
    Matching is by title+artist and (when known) duration within MAX_DURATION_DRIFT_S.
    """
    settings = get_settings()
    if not settings.allow_ytdlp_sourcing:
        raise SourceError(
            "yt-dlp sourcing is disabled (ALLOW_YTDLP_SOURCING=false). "
            "Use source_url with content you have the right to download."
        )

    import yt_dlp

    query = f"ytsearch{settings.source_search_results}:{meta.search_query}"
    # Flat extraction: list candidates (id/title/duration) WITHOUT resolving formats,
    # so one bad result can't abort the search. Formats are resolved only for the pick.
    search_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(search_opts) as ydl:
        result = ydl.extract_info(query, download=False)
    entries = (result or {}).get("entries") or []
    chosen = _best_match(entries, meta)
    if not chosen:
        raise SourceError("No source found for this track")

    vid = chosen.get("id") or chosen.get("url") or ""
    page_url = vid if str(vid).startswith("http") else f"https://www.youtube.com/watch?v={vid}"
    return download_direct(page_url, dest_dir, basename)
