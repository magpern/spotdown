"""Lifetimes of the source-match cache and the cached-analysis summary of a match candidate. Pure.

classbeat plan §5: the source-match cache is global (public search results), keyed on the
normalised metadata fingerprint plus ``matcher_version`` (``normalise_policy.fingerprint``); it is
kept for days, and a miss for a shorter time so a new upload is found soon.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

PROVIDER = "youtube"  # the backoff key of the one provider
HIT_TTL_S = 3 * 24 * 3600.0
MISS_TTL_S = 6 * 3600.0


def ttl_for(candidate_count: int) -> float:
    return HIT_TTL_S if candidate_count > 0 else MISS_TTL_S


def retry_after(until: float, now: float) -> Optional[float]:
    """Whole seconds left of a provider backoff, or None when it is over."""
    left = until - now
    return float(math.ceil(left)) if left > 0 else None


def cached_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The small, free summary of a cached analysis (match-v1 ``cached_summary``): no audio, no
    names, no provenance."""
    grid = doc.get("grid") or {}
    summary: Dict[str, Any] = {
        "analyzer_version": doc["provenance"]["analyzer"]["analyzer_version"],
        "bpm": doc["tempo"]["bpm"],
        "bpm_confidence": doc["tempo"]["confidence"],
        "duration_s": doc["signal"]["duration_s"],
        "has_beats": bool((grid.get("beats") or {}).get("times_s")),
        "has_downbeats": bool((grid.get("downbeats") or {}).get("times_s")),
        "has_events": bool(doc.get("events")),
    }
    return summary
