"""Text normalisation for recording matching: titles, artists, version markers, fingerprints. Pure.

A version marker (live, remix, sped up, ...) present on one side only means another recording.
Noise (official video, lyrics, remastered, HD) is dropped: it names the upload, not the recording.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Dict, List, Optional, Sequence

# Always used with fullmatch: "$" alone also matches before a final newline.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}")

# Phrase -> marker. Multi-word phrases first, so "slowed reverb" is not also two markers' parts.
_MARKERS: Dict[str, str] = {
    "sped up": "sped_up",
    "speed up": "sped_up",
    "slowed down": "slowed",
    "slowed": "slowed",
    "reverb": "reverb",
    "nightcore": "nightcore",
    "live": "live",
    "unplugged": "acoustic",
    "acoustic": "acoustic",
    "remix": "remix",
    "rmx": "remix",
    "cover": "cover",
    "karaoke": "karaoke",
    "instrumental": "instrumental",
    "extended": "extended",
    "radio edit": "radio_edit",
    "8d": "8d",
    "bass boosted": "bass_boosted",
    "mashup": "mashup",
    "demo": "demo",
    "re recorded": "re_recorded",
    "rerecorded": "re_recorded",
    "re record": "re_recorded",
    "version": "version",
    "mix": "mix",
    "edit": "edit",
    "vip": "vip",
    "rework": "rework",
    "bootleg": "bootleg",
    "lo fi": "lofi",
    "lofi": "lofi",
    "daycore": "daycore",
    "piano": "piano",
}
# Qualifiers that name the release itself, not another recording: dropped before markers count.
_NEUTRAL = re.compile(
    r"\b(original mix|original version|album version|single version|"
    r"(\d{4} )?remaster(ed)?( \d{4})? version)\b"
)
_MARKER_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _MARKERS) + r")\b")

_NOISE = re.compile(
    r"\b("
    r"official (music )?(video|audio|visualizer|lyric video)|lyric video|lyrics?|"
    r"music video|video oficial|audio oficial|visualizer|"
    r"(\d{4} )?remaster(ed)?( \d{4})?( version)?|"
    r"explicit|clean|hd|hq|4k|full song|audio|video|official"
    r")\b"
)
_FEAT = re.compile(r"\b(feat|ft|featuring)\b.*$")
_TOPIC = " - topic"


def fold(text: str) -> str:
    """Lower case, accents removed, ``&`` as ``and``, punctuation as spaces, single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ").replace("+", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(text: str) -> str:
    """The title with featured artists and upload noise removed (markers are kept)."""
    folded = fold(text)
    folded = _FEAT.sub("", folded)
    folded = _NEUTRAL.sub(" ", folded)
    folded = _NOISE.sub(" ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def markers(text: str) -> Dict[str, int]:
    """How often each version marker occurs in the folded text."""
    counts: Dict[str, int] = {}
    for found in _MARKER_RE.findall(_NEUTRAL.sub(" ", fold(text))):
        name = _MARKERS[found]
        counts[name] = counts.get(name, 0) + 1
    return counts


def strip_first(text: str, phrase: str) -> str:
    """``text`` (folded) without the first whole-word occurrence of ``phrase`` (folded)."""
    phrase = fold(phrase)
    if not phrase:
        return text
    return re.sub(r"\s+", " ", re.sub(rf"\b{re.escape(phrase)}\b", " ", text, count=1)).strip()


def candidate_title(title: str, artists: Sequence[str]) -> str:
    """A candidate's folded title with the first mention of each requested artist removed, so an
    "Artist - Title" upload compares with the bare title, and a band named Live is no marker."""
    text = fold(title)
    for artist in artists:
        text = strip_first(text, artist)
    return text


def channel_artist(channel: str) -> str:
    """The folded artist a channel name stands for: "Artist - Topic", "ArtistVEVO", "Artist"."""
    name = (channel or "").strip()
    if name.lower().endswith(_TOPIC):
        name = name[: -len(_TOPIC)]
    name = re.sub(r"vevo$", "", name, flags=re.I)
    return re.sub(r"\b(official|music|channel)\b", " ", fold(name)).strip()


def is_topic_channel(channel: str) -> bool:
    """YouTube "... - Topic" channels are auto-generated release masters."""
    return (channel or "").strip().lower().endswith(_TOPIC)


def compact(text: str) -> str:
    """Folded text without spaces: "TheWeekndVEVO" names the same artist as "The Weeknd"."""
    return text.replace(" ", "")


def tokens(text: str) -> List[str]:
    return [t for t in text.split(" ") if t]


def normal_isrc(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned if ISRC.fullmatch(cleaned) else None


def search_query(title: str, artists: Sequence[str]) -> str:
    """The normalised search query: the primary artist, then the clean title."""
    primary = fold(artists[0]) if artists else ""
    return " ".join(p for p in (primary, clean_title(title)) if p)


def duration_bucket(duration_s: float) -> int:
    return int(round(duration_s / 5.0))


def fingerprint(
    *,
    title: str,
    artists: Sequence[str],
    duration_s: float,
    isrc: Optional[str],
    matcher_version: str,
) -> str:
    """The source-match cache key: the ISRC, or title, artists and duration bucket, plus the
    matcher version (plan §5). Normalised, so spelling variants share one entry."""
    code = normal_isrc(isrc)
    if code:
        identity: List[object] = ["isrc", code]
    else:
        identity = [
            "meta",
            clean_title(title),
            sorted(fold(a) for a in artists),
            duration_bucket(duration_s),
        ]
    payload = json.dumps([matcher_version, identity], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
