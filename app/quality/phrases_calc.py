"""Scores phrases against annotated truth phrases (pure).

A phrase is right when both its start and its end are within the tolerance of a truth phrase's
(which also fixes its length: 8 or 16 bars), matched one to one. Precision and recall are pooled
over the corpus, so a track with no truth phrases that gets one anyway costs precision, and a
track with truth phrases that gets none costs recall.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple


def estimated(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The phrases of an analysis document as ``{start, end, bars, confidence}``."""
    return [
        {"start": p["start_s"], "end": p["end_s"], "bars": p["bars"], "confidence": p["confidence"]}
        for p in doc.get("grid", {}).get("phrases", [])
    ]


def match(
    truth: Sequence[Dict[str, Any]], est: Sequence[Dict[str, Any]], tol_s: float
) -> List[Tuple[float, bool]]:
    """``(confidence, matched)`` for every estimated phrase, one truth phrase per estimate."""
    pairs = sorted(
        (abs(t["start"] - e["start"]) + abs(t["end"] - e["end"]), i, j)
        for i, t in enumerate(truth)
        for j, e in enumerate(est)
        if abs(t["start"] - e["start"]) <= tol_s and abs(t["end"] - e["end"]) <= tol_s
    )
    used_t, used_e = set(), set()
    for _, i, j in pairs:
        if i not in used_t and j not in used_e:
            used_t.add(i)
            used_e.add(j)
    return [(e["confidence"], j in used_e) for j, e in enumerate(est)]
