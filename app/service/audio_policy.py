"""Rules of the mp3 route (``POST /v2/audio``, ``GET /v2/audio/{id}``; ADR 0005 Decision 5). Pure.

The audio id is made by the server (``new_audio_id``) and checked by ``AUDIO_ID`` before it names a
file; no path is ever built from request input. The ID3 text and the download's file name come
from the caller's title and artists: control characters are dropped and the lengths capped, and the
file name is plain ASCII from a short safe set, so it needs no quoting or escaping in a header.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import List, Optional, Sequence

BITRATES = (128, 192, 256, 320)
DEFAULT_BITRATE = 320
AUDIO_ID = re.compile(r"au_[0-9a-f]{32}")
TITLE_MAX = 200
ARTIST_MAX = 100
ARTISTS_MAX = 10
FILENAME_MAX = 120
_UNSAFE = re.compile(r"[^A-Za-z0-9 ,_()-]+")
_WORD = re.compile(r"[A-Za-z0-9]")
_SPACES = re.compile(r"\s+")


def new_audio_id() -> str:
    return "au_" + uuid.uuid4().hex


def is_audio_id(value: str) -> bool:
    return AUDIO_ID.fullmatch(value) is not None


def clean_text(value: Optional[str], cap: int) -> str:
    """``value`` without control, format or separator-line characters, whitespace collapsed,
    at most ``cap`` characters."""
    out: List[str] = []
    for ch in value or "":
        category = unicodedata.category(ch)
        if ch in "\t\n\r" or category in ("Zl", "Zp"):
            out.append(" ")
        elif not category.startswith("C"):  # Cc, Cf, Cs, Co and Cn are dropped
            out.append(ch)
    return _SPACES.sub(" ", "".join(out)).strip()[:cap].strip()


def clean_artists(values: Optional[Sequence[str]]) -> List[str]:
    cleaned = (clean_text(v, ARTIST_MAX) for v in (values or [])[:ARTISTS_MAX])
    return [a for a in cleaned if a]


def file_name(title: str, artists: Sequence[str], video_id: str) -> str:
    """A safe ASCII ``.mp3`` name: ``Artist - Title``, else ``youtube-<video id>``."""
    label = " - ".join(p for p in (", ".join(artists), title) if p)
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    safe = _SPACES.sub(" ", _UNSAFE.sub(" ", ascii_label)).strip(" ,-")
    safe = safe[:FILENAME_MAX].rstrip(" ,-")
    return (safe if _WORD.search(safe) else f"youtube-{video_id}") + ".mp3"


def expires_at(now: float, ttl_s: float) -> float:
    return now + ttl_s


def is_kept(expires: float, now: float) -> bool:
    """A file is served and reused only before it expires."""
    return now < expires


def job_record_ttl_s(audio_ttl_s: float, idempotency_window_s: float) -> float:
    """How long a finished audio job's record (and its title and artists) is kept: the file's
    lifetime or the idempotency window, whichever is longer, so a replayed job id still polls."""
    return max(audio_ttl_s, idempotency_window_s)
