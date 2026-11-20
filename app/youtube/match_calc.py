"""Recording-match confidence, bands, duration checks and the match decision. Pure.

classbeat plan §2: positive signals are an equal ISRC, a Topic or official artist channel and an
equal normalised title and artist; a version marker on one side only is a negative signal, and so
is the duration. Bands: ``confident`` >= 0.85, ``uncertain`` 0.6 to 0.85, below ``not_same``.
A wrong version is never ``confident``: a marker mismatch caps the score below 0.6.

Every YouTube match is timing-approximate for the caller; this module only reports the evidence
(the recording-match confidence and the duration checks) and never claims exact audio identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import normalise_policy as norm

MATCHER_VERSION = "youtube-1"
CONFIDENT = 0.85
UNCERTAIN = 0.6
OK_S = 2.0
TOLERABLE_S = 5.0
MAX_ALTERNATES = 2

# Caps (an upper bound on the score) for the negative signals.
CAP_MARKER_MISMATCH = 0.5
CAP_DURATION_REJECTED = 0.55
CAP_ISRC_DIFFERS = 0.84
CAP_TITLE_DIFFERS = 0.59
CAP_EXTRA_WORDS = 0.84  # a candidate title with words the requested one lacks: "Hello Goodbye"


@dataclass(frozen=True)
class Wanted:
    title: str
    artists: Tuple[str, ...]
    duration_s: float
    isrc: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    channel: str
    duration_s: float
    rank: int
    isrc: Optional[str] = None
    channel_verified: bool = False


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    confidence: float
    band: str
    delta_s: float
    check: str
    signals: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    status: str  # matched, uncertain or not_found
    best: Optional[Scored]
    alternates: List[Scored]
    scored: List[Scored]


def band_for(confidence: float) -> str:
    if confidence >= CONFIDENT:
        return "confident"
    if confidence >= UNCERTAIN:
        return "uncertain"
    return "not_same"


def duration_check(delta_s: float) -> str:
    """|delta| <= 2 s ``ok``, <= 5 s ``tolerable``, above ``rejected``."""
    gap = abs(delta_s)
    if gap <= OK_S:
        return "ok"
    if gap <= TOLERABLE_S:
        return "tolerable"
    return "rejected"


def candidate_from_entry(entry: Any, rank: int) -> Optional[Candidate]:
    """A search entry as a candidate, or None when it cannot be validated before download: no
    well-formed 11-character video id, no duration, or a live stream."""
    if not isinstance(entry, dict):
        return None
    video_id = entry.get("id")
    duration = entry.get("duration")
    if not isinstance(video_id, str) or not norm.VIDEO_ID.fullmatch(video_id):
        return None
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        return None
    if entry.get("live_status") in ("is_live", "is_upcoming") or entry.get("is_live"):
        return None
    channel = entry.get("channel") or entry.get("uploader") or ""
    return Candidate(
        video_id=video_id,
        title=str(entry.get("title") or ""),
        channel=str(channel),
        duration_s=float(duration),
        rank=rank,
        isrc=norm.normal_isrc(entry.get("isrc")),
        channel_verified=bool(entry.get("channel_is_verified")),
    )


def _title_words(wanted: Wanted, cand: Candidate) -> Tuple[set, set]:
    want = set(norm.tokens(norm.clean_title(wanted.title)))
    have = set(norm.tokens(norm.clean_title(norm.candidate_title(cand.title, wanted.artists))))
    return want, have


def _title_similarity(want: set, have: set) -> float:
    if not want or not have:
        return 0.0
    common = len(want & have)
    return 0.6 * common / len(want) + 0.4 * common / len(have)


def _artist_score(wanted: Wanted, cand: Candidate) -> float:
    title = norm.fold(cand.title)
    channel = norm.compact(norm.channel_artist(cand.channel))
    for i, artist in enumerate(wanted.artists):
        name = norm.fold(artist)
        if name and (norm.compact(name) == channel or norm.strip_first(title, name) != title):
            return 1.0 if i == 0 else 0.5
    return 0.0


def _channel_score(wanted: Wanted, cand: Candidate) -> float:
    names = {norm.compact(norm.fold(a)) for a in wanted.artists}
    if norm.compact(norm.channel_artist(cand.channel)) not in names:
        return 0.0
    if norm.is_topic_channel(cand.channel):
        return 1.0
    # Anyone can name a channel after an artist (or "ArtistVEVO"): only a verified one counts.
    return 0.9 if cand.channel_verified else 0.0


def _marker_mismatch(wanted: Wanted, cand: Candidate) -> List[str]:
    want = norm.markers(wanted.title)
    have = norm.markers(norm.candidate_title(cand.title, wanted.artists))
    return sorted(m for m in set(want) | set(have) if want.get(m, 0) != have.get(m, 0))


def score(wanted: Wanted, cand: Candidate) -> Scored:
    delta = round(cand.duration_s - wanted.duration_s, 3)
    check = duration_check(delta)
    want_words, have_words = _title_words(wanted, cand)
    title = _title_similarity(want_words, have_words)
    artist = _artist_score(wanted, cand)
    channel = _channel_score(wanted, cand)
    dur = {"ok": 1.0 - 0.1 * abs(delta) / OK_S, "tolerable": 0.5, "rejected": 0.0}[check]
    # Without a channel signal the best possible score is 0.8: a plain upload is never confident.
    value = 0.4 * title + 0.25 * artist + 0.2 * channel + 0.15 * dur
    want_isrc = norm.normal_isrc(wanted.isrc)
    isrc = None if not (want_isrc and cand.isrc) else want_isrc == cand.isrc
    mismatch = _marker_mismatch(wanted, cand)
    if isrc is True and title >= 0.5:
        value = max(value, 0.95)
    caps = [1.0]
    if mismatch:
        caps.append(CAP_MARKER_MISMATCH)
    if check == "rejected":
        caps.append(CAP_DURATION_REJECTED)
    if isrc is False:
        caps.append(CAP_ISRC_DIFFERS)
    if title < 0.5:
        caps.append(CAP_TITLE_DIFFERS)
    if have_words - want_words:
        caps.append(CAP_EXTRA_WORDS)
    confidence = round(max(0.0, min(value, *caps)), 3)
    signals = {
        "title": round(title, 3),
        "artist": artist,
        "channel": channel,
        "isrc_equal": isrc,
        "version_mismatch": mismatch,
        "extra_words": sorted(have_words - want_words),
    }
    return Scored(cand, confidence, band_for(confidence), delta, check, signals)


def decide(wanted: Wanted, candidates: Sequence[Candidate]) -> Decision:
    """Ranks the candidates. ``matched`` needs a ``confident`` best; ``uncertain`` an
    ``uncertain`` one; a best that is ``not_same`` (or no candidate at all) is ``not_found``.
    Alternates are the next plausible candidates (not ``not_same``), at most two."""
    scored = sorted(
        (score(wanted, c) for c in candidates),
        key=lambda s: (-s.confidence, abs(s.delta_s), s.candidate.rank),
    )
    if not scored or scored[0].band == "not_same":
        return Decision("not_found", None, [], scored)
    best = scored[0]
    alternates = [s for s in scored[1:] if s.band != "not_same"][:MAX_ALTERNATES]
    status = "matched" if best.band == "confident" else "uncertain"
    return Decision(status, best, alternates, scored)


def match_record(query: str, decision: Decision) -> Optional[Dict[str, Any]]:
    """The provenance match record (analysis-v1 ``match_record``) of a decision with a best."""
    if decision.best is None:
        return None
    return {
        "matcher_version": MATCHER_VERSION,
        "normalised_query": query,
        "candidates": [
            {"ref": s.candidate.video_id, "score": s.confidence, "duration_delta_s": s.delta_s}
            for s in decision.scored
        ],
        "chosen": decision.best.candidate.video_id,
    }


def decoded_duration_check(
    expected_s: float, expected_from: str, file_s: float, content_s: float
) -> Dict[str, Any]:
    """The second duration check, on the decoded audio: both the untrimmed file and the
    silence-trimmed content are compared, and the closer one decides."""
    file_delta = round(file_s - expected_s, 3)
    content_delta = round(content_s - expected_s, 3)
    closest = min(abs(file_delta), abs(content_delta))
    return {
        "expected_s": round(expected_s, 3),
        "expected_from": expected_from,
        "file_delta_s": file_delta,
        "content_delta_s": content_delta,
        "result": duration_check(closest),
    }
