"""Spotify link parsing, page URLs and the resolve answer of ``POST /v2/spotify/resolve``
(ADR 0005, Decision 4; spotdown#76 as revised). No I/O.

The caller's link is reduced to a (kind, id) pair and nothing else of it is kept. The only URLs
fetched are built here from that pair, on ``open.spotify.com`` (Spotify's public oEmbed endpoint
and embed page; no credentials, no Web API), so the caller's URL is never fetched as given.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

HOST = "open.spotify.com"
MAX_TRACKS = 500
MAX_BODY_BYTES = 5 * 1024 * 1024

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


def is_allowed_url(url: str) -> bool:
    """Only https on ``open.spotify.com`` itself: no userinfo, no port."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == HOST


def answer(tracks: List[Dict[str, Any]], total: Optional[int], kind: str) -> Dict[str, Any]:
    """The resolve answer: at most MAX_TRACKS tracks, ``listed`` (the usable tracks the page
    listed), ``total`` (what the page says there are, when it says) and ``truncated`` (some were
    left out: over the cap, or fewer listed than the stated total)."""
    listed = len(tracks)
    if kind == "track":
        total = listed
    truncated = listed > MAX_TRACKS or (total is not None and total > listed)
    return {
        "tracks": tracks[:MAX_TRACKS],
        "listed": listed,
        "total": total,
        "truncated": truncated,
    }
