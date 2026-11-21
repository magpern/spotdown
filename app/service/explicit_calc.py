"""The explicit-content signal of an analysis (``content.explicit``, spotdown#36). Pure.

Report only: nothing in spotdown blocks, filters or ranks on it; the consumer decides. Phase 1 uses
metadata alone (``method`` ``metadata``), no audio model:

- ``caller``: the ``explicit`` flag of the analysis request;
- ``caller_match``: the ``explicit`` flag of the ``/v2/match`` item that chose the video;
- ``youtube_age_limit``: the video's yt-dlp ``age_limit`` when it is 18 or more (``yes``). A lower
  age limit is no evidence either way, so it is not recorded.

The value is ``unknown`` without evidence, never ``no``. Any ``yes`` evidence makes it ``yes``
(a caution flag errs toward flagging); against ``no`` evidence its confidence is capped at
``CONFLICT_CAP``. The confidence is the strongest weight on the winning side, not a product of
them: the two caller flags usually repeat one catalogue flag and are not independent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

METHOD = "metadata"
CALLER = "caller"
CALLER_MATCH = "caller_match"
AGE_LIMIT = "youtube_age_limit"
ADULT_AGE = 18
# How far each piece of evidence is trusted, by (source, says yes). First documented estimates.
WEIGHTS: Dict[tuple, float] = {
    (CALLER, True): 0.9,
    (CALLER, False): 0.8,
    (CALLER_MATCH, True): 0.9,
    (CALLER_MATCH, False): 0.8,
    (AGE_LIMIT, True): 0.6,  # an age restriction can be for the video, not the lyrics
}
CONFLICT_CAP = 0.5


def _flag(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _age(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= ADULT_AGE else None


def gather(
    *, caller: Any = None, caller_match: Any = None, age_limit: Any = None
) -> List[Dict[str, Any]]:
    """The evidence list, in a fixed order. Anything that is not a bool flag or an integer age
    limit of 18 or more is no evidence and is left out."""
    found: List[Dict[str, Any]] = []
    for source, flag in ((CALLER, _flag(caller)), (CALLER_MATCH, _flag(caller_match))):
        if flag is not None:
            found.append({"source": source, "value": flag})
    age = _age(age_limit)
    if age is not None:
        found.append({"source": AGE_LIMIT, "value": age})
    return found


def _says_yes(item: Dict[str, Any]) -> bool:
    return item["source"] == AGE_LIMIT or item["value"] is True


def explicit(evidence: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """``content.explicit`` from the evidence: value, confidence, method and the evidence kept."""
    yes = [WEIGHTS[(e["source"], True)] for e in evidence if _says_yes(e)]
    no = [WEIGHTS[(e["source"], False)] for e in evidence if not _says_yes(e)]
    if yes:
        value, confidence = "yes", max(yes)
        if no:
            confidence = min(confidence, CONFLICT_CAP)
    elif no:
        value, confidence = "no", max(no)
    else:
        value, confidence = "unknown", 0.0
    return {
        "value": value,
        "confidence": confidence,
        "method": METHOD,
        "evidence": [dict(e) for e in evidence],
    }


def content(evidence: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The analysis' ``content`` group."""
    return {"explicit": explicit(evidence)}
