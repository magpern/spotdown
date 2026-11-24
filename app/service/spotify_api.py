"""Track rows from Spotify Web API JSON, for the credentials path of ``POST /v2/spotify/resolve``
(spotdown#76). No I/O.

The same fields as ``app/spotify.py``'s ``_track_from_api`` (name, artists, album, duration,
ISRC), in the resolve answer's shape and with every field checked: a missing or mistyped field is
``None`` or ``[]``, and a row that is not a catalogue track (an episode, a local file, a row
without an id) is skipped.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .spotify_policy import ID

MAX_TITLE = 300
MAX_ARTIST = 200
MAX_ARTISTS = 10
MAX_ALBUM = 300
MAX_ISRC = 32


def _text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def page_rows(page: Any) -> Tuple[List[Any], bool, Optional[int]]:
    """The rows of a paging object, whether Spotify says more follow (its ``next`` is read as a
    flag only, never fetched) and its ``total``. Raises ValueError for anything else."""
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise ValueError("not a paging object")
    total = page.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        total = None
    return page["items"], bool(page.get("next")), total


def track_of(item: Any, album: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or item.get("type", "track") != "track":
        return None
    sid = item.get("id")
    title = _text(item.get("name"), MAX_TITLE)
    if not isinstance(sid, str) or not ID.fullmatch(sid) or title is None:
        return None
    artists: List[str] = []
    for artist in item.get("artists") or []:
        name = _text(artist.get("name"), MAX_ARTIST) if isinstance(artist, dict) else None
        if name is not None:
            artists.append(name)
    own_album = item.get("album")
    if isinstance(own_album, dict):
        album = _text(own_album.get("name"), MAX_ALBUM) or album
    duration = item.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        duration = None
    ids = item.get("external_ids")
    return {
        "spotify_id": sid,
        "title": title,
        "artists": artists[:MAX_ARTISTS],
        "album": album,
        "duration_ms": duration,
        "isrc": _text(ids.get("isrc"), MAX_ISRC) if isinstance(ids, dict) else None,
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
