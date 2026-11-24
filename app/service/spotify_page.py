"""Track metadata from Spotify's public pages: the embed page's embedded JSON and the oEmbed
JSON (spotdown#76 as revised). No I/O.

**An unverified assumption until the owner tries it live.** No agent or CI fetches a real Spotify
page, so the shape read here is the one observed and documented for the embed page (a Next.js
``__NEXT_DATA__`` script whose ``props.pageProps.state.data.entity`` holds ``name``/``title``,
``artists``, ``duration`` and a ``trackList`` of ``{uri, title, subtitle, duration}``). Everything
is optional: a missing field is ``None`` or ``[]``, never an error. Only a page with no usable
track at all gives nothing, and the route reports that.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .spotify_policy import ID

_SCRIPT_OPEN = b"<script"
_SCRIPT_CLOSE = b"</script>"
_NEXT_DATA_ID = b'id="__next_data__"'  # matched against the lower-cased page
_WORD_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_")  # bytes \w, lower-cased
_TRACK_URI = re.compile(r"spotify:track:([0-9A-Za-z]{22})")
_TOTAL_KEYS = ("totalCount", "trackCount", "total")
_ARTIST_SEP = re.compile(r",\s*")  # str pattern: \s is Unicode, so U+00A0 is whitespace
MAX_SEARCH_DEPTH = 12

MAX_TITLE = 300
MAX_ARTIST = 200
MAX_ARTISTS = 10
MAX_ALBUM = 300


def _text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_json(body: bytes) -> Any:
    """An upstream body as JSON. Every parse failure is a ``ValueError``: bad UTF-8, bad JSON,
    and nesting too deep for the decoder (``RecursionError``, which is not a ``ValueError``)."""
    try:
        return json.loads(body.decode("utf-8"))
    except RecursionError:
        raise ValueError("nested too deeply") from None


def _is_next_data_tag(lower: bytes, start: int, end: int) -> bool:
    """Whether the lower-cased opening tag ``lower[start:end]`` (``<script`` up to its ``>``)
    holds ``id="__next_data__"`` after a non-word byte, as the former ``\\bid=`` required."""
    at = lower.find(_NEXT_DATA_ID, start, end)
    while at != -1:
        if lower[at - 1] not in _WORD_BYTES:  # at > start: the tag begins with "<script"
            return True
        at = lower.find(_NEXT_DATA_ID, at + 1, end)
    return False


def _next_data_body(html: bytes) -> Optional[bytes]:
    """The text of the first ``<script ... id="__NEXT_DATA__" ...>`` up to the next
    ``</script>``, case-insensitive. Linear time: every search starts after the previous tag."""
    lower = html.lower()  # ASCII only, so every offset is the same in ``html``
    at = lower.find(_SCRIPT_OPEN)
    while at != -1:
        end = lower.find(b">", at)
        if end == -1:
            return None
        if _is_next_data_tag(lower, at, end):
            close = lower.find(_SCRIPT_CLOSE, end + 1)
            return None if close == -1 else html[end + 1 : close]
        at = lower.find(_SCRIPT_OPEN, end + 1)
    return None


def next_data(html: bytes) -> Any:
    """The embed page's ``__NEXT_DATA__`` JSON, or None."""
    body = _next_data_body(html)
    if body is None:
        return None
    try:
        return parse_json(body)
    except ValueError:
        return None


def _walk(node: Any, depth: int = 0) -> Iterator[Dict[str, Any]]:
    if depth > MAX_SEARCH_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, depth + 1)


def entity(doc: Any, kind: str, sid: str) -> Optional[Dict[str, Any]]:
    """The page's entity: at the observed path, else the first object whose ``uri`` is the
    requested one."""
    path: Any = doc
    for key in ("props", "pageProps", "state", "data", "entity"):
        path = path.get(key) if isinstance(path, dict) else None
    if isinstance(path, dict):
        return path
    uri = f"spotify:{kind}:{sid}"
    return next((d for d in _walk(doc) if d.get("uri") == uri), None)


def _artists(item: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    listed = item.get("artists")
    if isinstance(listed, list):
        names = [
            n for a in listed if isinstance(a, dict) and (n := _text(a.get("name"), MAX_ARTIST))
        ]
    elif isinstance(item.get("subtitle"), str):
        # The track list gives the artists as one line, "A,\u00a0B" (live: a comma and a
        # no-break space; any whitespace is accepted). An artist whose own name holds a comma is
        # split too; the matcher scores on the first name.
        parts = _ARTIST_SEP.split(item["subtitle"])
        names = [n for part in parts if (n := _text(part, MAX_ARTIST))]
    return names[:MAX_ARTISTS]


def _duration(item: Dict[str, Any]) -> Optional[int]:
    for key in ("duration", "duration_ms"):
        value = _count(item.get(key))
        if value is not None:
            return value
    return None


def _track_id(item: Dict[str, Any], fallback: Optional[str]) -> Optional[str]:
    uri = item.get("uri")
    if isinstance(uri, str) and (m := _TRACK_URI.fullmatch(uri)):
        return m.group(1)
    sid = item.get("id")
    if isinstance(sid, str) and ID.fullmatch(sid):
        return sid
    return fallback


def track_of(
    item: Any, album: Optional[str] = None, fallback_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """One answer row, or None when the item is not a usable track (no id or no title, or an
    episode)."""
    if not isinstance(item, dict):
        return None
    kind = item.get("entityType", item.get("type", "track"))
    if kind != "track":
        return None
    sid = _track_id(item, fallback_id)
    title = _text(item.get("title"), MAX_TITLE) or _text(item.get("name"), MAX_TITLE)
    if sid is None or title is None:
        return None
    return {
        "spotify_id": sid,
        "title": title,
        "artists": _artists(item),
        "album": album,
        "duration_ms": _duration(item),
        "isrc": None,  # the public pages do not state one
    }


def embed_tracks(html: bytes, kind: str, sid: str) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """The usable tracks an embed page lists, in its order, and the total it states (or None)."""
    found = entity(next_data(html), kind, sid)
    if found is None:
        return [], None
    if kind == "track":
        track = track_of({**found, "entityType": "track"}, None, sid)
        return ([track] if track else []), None
    album = _text(found.get("name"), MAX_ALBUM) or _text(found.get("title"), MAX_ALBUM)
    album = album if kind == "album" else None
    rows = found.get("trackList")
    rows = rows if isinstance(rows, list) else []
    tracks = [t for t in (track_of(r, album) for r in rows) if t is not None]
    total = next((v for k in _TOTAL_KEYS if (v := _count(found.get(k))) is not None), None)
    return tracks, total


def oembed_track(body: bytes, sid: str) -> Optional[Dict[str, Any]]:
    """A track from the oEmbed JSON: its title only (oEmbed states no artist or duration)."""
    try:
        doc = parse_json(body)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    return track_of({"title": doc.get("title")}, None, sid)
