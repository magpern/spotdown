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


def _is_topic(e: dict) -> bool:
    """YouTube '... - Topic' channels are auto-generated release masters.

    They usually match the Spotify release within ~1s, which keeps the analysis
    timeline aligned to Spotify's.
    """
    ch = (e.get("channel") or e.get("uploader") or "").strip()
    return ch.endswith("- Topic")


def _closest_by_duration(entries: list, meta: TrackMeta):
    scored = []
    for e in entries:
        dur = e.get("duration")
        if dur and meta.duration_s:
            scored.append((abs(dur - meta.duration_s), e))
    if not scored:
        return None, None
    scored.sort(key=lambda x: x[0])
    return scored[0][1], scored[0][0]


def _best_match(entries: list, meta: TrackMeta) -> Optional[dict]:
    """Prefer a '- Topic' master, then closest-duration within tolerance, then top hit."""
    settings = get_settings()
    entries = [e for e in entries if e]
    if not entries:
        return None

    # 1) A Topic upload is almost always the right master — take its closest by duration.
    topic = [e for e in entries if _is_topic(e)]
    if topic:
        best, _ = _closest_by_duration(topic, meta)
        return best or topic[0]

    # 2) Otherwise the closest duration within tolerance.
    best, delta = _closest_by_duration(entries, meta)
    if best is not None and delta <= settings.max_duration_drift_s:
        return best

    # 3) Nothing reliable -> YouTube's top-ranked result.
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
