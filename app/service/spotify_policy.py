"""Spotify link parsing and track mapping for ``POST /v2/spotify/resolve`` (ADR 0005). No I/O.

The caller's link is reduced to a (kind, id) pair and nothing else of it is kept: the Web API
URLs are built here from fixed hosts, so the caller's URL is never fetched (Decision 4).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
ALLOWED_HOSTS = frozenset({"accounts.spotify.com", "api.spotify.com"})

MAX_TRACKS = 500
# Rows read at most (some playlist rows are episodes or local files, not tracks): bounds the
# number of calls one request makes.
MAX_ROWS_SCANNED = 1000
PLAYLIST_PAGE = 100
ALBUM_PAGE = 50

KINDS = ("track", "album", "playlist")
_ID = re.compile(r"[0-9A-Za-z]{22}")
_URI = re.compile(r"spotify:(track|album|playlist):([0-9A-Za-z]{22})")
_PATH = re.compile(r"/(?:intl-[a-z]{2}/)?(track|album|playlist)/([0-9A-Za-z]{22})/?")
_LINK_HOST = "open.spotify.com"
_SAFE = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no space, no control character

MAX_TITLE = 300
MAX_ARTIST = 200
MAX_ARTISTS = 10
MAX_ALBUM = 300
MAX_ISRC = 32


class LinkError(ValueError):
    """The input is not a Spotify track, album or playlist link. Never carries the input."""

    def __init__(self) -> None:
        super().__init__("not a Spotify track, album or playlist link")


def parse_link(value: str) -> Tuple[str, str]:
    """``(kind, id)`` of ``https://open.spotify.com/[intl-xx/]{kind}/<id>`` (scheme optional,
    query and fragment ignored) or ``spotify:{kind}:<id>``. Anything else raises LinkError: an
    other host, a userinfo, a port, a backslash, whitespace or a control character inside."""
    text = value.strip()
    if not _SAFE.fullmatch(text) or "\\" in text:
        raise LinkError()
    uri = _URI.fullmatch(text)
    if uri:
        return uri.group(1), uri.group(2)
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        raise LinkError() from None
    # The whole authority must be exactly the link host: no userinfo, no port, no suffix.
    if parts.scheme.lower() not in ("https", "http") or parts.netloc.lower() != _LINK_HOST:
        raise LinkError()
    path = _PATH.fullmatch(parts.path)
    if not path:
        raise LinkError()
    return path.group(1), path.group(2)


def first_url(kind: str, sid: str) -> str:
    """The first Web API URL for a parsed link, on the fixed API host."""
    if kind not in KINDS or not _ID.fullmatch(sid):
        raise LinkError()
    if kind == "track":
        return f"{API_BASE}/tracks/{sid}"
    if kind == "album":
        return f"{API_BASE}/albums/{sid}"
    return page_url(kind, sid, 0)


def page_url(kind: str, sid: str, offset: int) -> str:
    """A page of an album's or a playlist's tracks. Spotify's own ``next`` is never followed."""
    if kind not in ("album", "playlist") or not _ID.fullmatch(sid) or offset < 0:
        raise LinkError()
    limit = ALBUM_PAGE if kind == "album" else PLAYLIST_PAGE
    base = "albums" if kind == "album" else "playlists"
    return f"{API_BASE}/{base}/{sid}/tracks?limit={limit}&offset={int(offset)}"


def page_rows(page: Any) -> Tuple[List[Any], bool]:
    """The rows of a paging object and whether Spotify says more follow."""
    if not isinstance(page, dict):
        raise ValueError("not a paging object")
    items = page.get("items")
    if not isinstance(items, list):
        raise ValueError("not a paging object")
    return items, bool(page.get("next"))


def _text(value: Any, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) and value else None


def track_of(item: Any, album: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One answer row from a Spotify track object, or None when it is not a catalogue track (an
    episode, a local file, a row without an id)."""
    if not isinstance(item, dict) or item.get("type", "track") != "track":
        return None
    sid = item.get("id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        return None
    artists = [
        name
        for a in (item.get("artists") or [])
        if isinstance(a, dict) and (name := _text(a.get("name"), MAX_ARTIST))
    ][:MAX_ARTISTS]
    own_album = item.get("album")
    if isinstance(own_album, dict):
        album = _text(own_album.get("name"), MAX_ALBUM) or album
    duration = item.get("duration_ms")
    ids = item.get("external_ids")
    isrc = _text(ids.get("isrc"), MAX_ISRC) if isinstance(ids, dict) else None
    return {
        "spotify_id": sid,
        "title": _text(item.get("name"), MAX_TITLE) or "",
        "artists": artists,
        "album": album,
        "duration_ms": duration
        if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0
        else None,
        "isrc": isrc,
    }


def playlist_tracks(rows: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        track = track_of(row.get("track")) if isinstance(row, dict) else None
        if track is not None:
            out.append(track)
    return out


def album_tracks(rows: List[Any], album: Optional[str]) -> List[Dict[str, Any]]:
    return [t for t in (track_of(row, album) for row in rows) if t is not None]


def cap(tracks: List[Dict[str, Any]], more: bool) -> Tuple[List[Dict[str, Any]], bool]:
    """At most MAX_TRACKS tracks, and whether any were left out."""
    return tracks[:MAX_TRACKS], more or len(tracks) > MAX_TRACKS
