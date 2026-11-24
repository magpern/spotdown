"""Spotify link parsing, page URLs and the resolve answer of ``POST /v2/spotify/resolve``
(ADR 0005, Decision 4; spotdown#76 as revised). No I/O.

The caller's link is reduced to a (kind, id) pair and nothing else of it is kept. The only URLs
fetched are built here from that pair, so the caller's URL is never fetched as given:
- with the backend's Spotify credentials, the Web API on ``accounts.spotify.com`` and
  ``api.spotify.com`` (``API_HOSTS``);
- without them (the default), Spotify's public oEmbed endpoint and embed page on
  ``open.spotify.com`` (``PAGE_HOSTS``).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

HOST = "open.spotify.com"
PAGE_HOSTS = frozenset({HOST})
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
API_HOSTS = frozenset({"accounts.spotify.com", "api.spotify.com"})
MAX_TRACKS = 500
MAX_BODY_BYTES = 5 * 1024 * 1024  # every upstream response, on both paths
# API rows read at most (some playlist rows are episodes or local files): bounds the calls.
MAX_ROWS_SCANNED = 1000
PLAYLIST_PAGE = 100
ALBUM_PAGE = 50

KINDS = ("track", "album", "playlist")
ID = re.compile(r"[0-9A-Za-z]{22}")
_URI = re.compile(r"spotify:(track|album|playlist):([0-9A-Za-z]{22})")
_PATH = re.compile(r"/(?:intl-[a-z]{2}/)?(track|album|playlist)/([0-9A-Za-z]{22})/?")
_SAFE = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no space, no control character


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
    if parts.scheme.lower() not in ("https", "http") or parts.netloc.lower() != HOST:
        raise LinkError()
    path = _PATH.fullmatch(parts.path)
    if not path:
        raise LinkError()
    return path.group(1), path.group(2)


def _checked(kind: str, sid: str) -> None:
    if kind not in KINDS or not ID.fullmatch(sid):
        raise LinkError()


def embed_url(kind: str, sid: str) -> str:
    _checked(kind, sid)
    return f"https://{HOST}/embed/{kind}/{sid}"


def oembed_url(kind: str, sid: str) -> str:
    _checked(kind, sid)
    return f"https://{HOST}/oembed?url=" + quote(f"https://{HOST}/{kind}/{sid}", safe="")


def api_first_url(kind: str, sid: str) -> str:
    """The first Web API URL for a parsed link, on the fixed API host."""
    _checked(kind, sid)
    if kind == "track":
        return f"{API_BASE}/tracks/{sid}"
    if kind == "album":
        return f"{API_BASE}/albums/{sid}"
    return api_page_url(kind, sid, 0)


def api_page_url(kind: str, sid: str, offset: int) -> str:
    """A page of an album's or a playlist's tracks. Spotify's own ``next`` is never followed."""
    _checked(kind, sid)
    if kind == "track" or offset < 0:
        raise LinkError()
    limit = ALBUM_PAGE if kind == "album" else PLAYLIST_PAGE
    base = "albums" if kind == "album" else "playlists"
    return f"{API_BASE}/{base}/{sid}/tracks?limit={limit}&offset={int(offset)}"


def is_allowed_url(url: str, hosts: frozenset = PAGE_HOSTS) -> bool:
    """Only https on one of ``hosts`` itself: no userinfo, no port."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc in hosts


def answer(
    tracks: List[Dict[str, Any]],
    total: Optional[int],
    kind: str,
    more: Optional[bool] = None,
) -> Dict[str, Any]:
    """The resolve answer: at most MAX_TRACKS tracks, ``listed`` (the usable tracks read),
    ``total`` (what Spotify says there are, when it says) and ``truncated`` (some were left out:
    over the cap, or, for a page, fewer listed than the stated total; for the API, rows left
    unread, ``more``)."""
    listed = len(tracks)
    if kind == "track":
        total = listed
    short = (total is not None and total > listed) if more is None else more
    truncated = listed > MAX_TRACKS or short
    return {
        "tracks": tracks[:MAX_TRACKS],
        "listed": listed,
        "total": total,
        "truncated": truncated,
    }
