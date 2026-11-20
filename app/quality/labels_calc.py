"""Scores section labels against annotated truth labels (pure).

Two views of the same comparison, both limited to the track's content range (the silence around it
is not music and carries no truth label):

* **frame accuracy**: the share of 0.5 s frames whose label equals the truth label there. A frame
  with no estimated label is wrong, so leaving labels out never scores.
* **segment correctness**: for each labelled segment, whether its label is the truth label that
  covers most of it. These, with the label confidences, give the calibration error.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

FRAME_S = 0.5


def estimated_spans(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The labelled segments of an analysis document as ``{start, end, label, confidence}``."""
    by_id = {s["id"]: s for s in doc["structure"]["segments"]}
    return [
        {
            "start": by_id[x["segment_id"]]["start_s"],
            "end": by_id[x["segment_id"]]["end_s"],
            "label": x["label"],
            "confidence": x["confidence"],
        }
        for x in doc.get("labels", [])
        if x["segment_id"] in by_id
    ]


def _at(spans: Sequence[Dict[str, Any]], t: float) -> Optional[str]:
    return next((s["label"] for s in spans if s["start"] <= t < s["end"]), None)


def frame_counts(
    truth: Sequence[Dict[str, Any]], est: Sequence[Dict[str, Any]], start: float, end: float
) -> Tuple[int, int]:
    """``(frames labelled correctly, frames in the content range)``."""
    n = int((end - start) / FRAME_S)
    hits = 0
    for k in range(n):
        t = start + (k + 0.5) * FRAME_S
        want = _at(truth, t)
        hits += want is not None and _at(est, t) == want
    return hits, n


def _majority(truth: Sequence[Dict[str, Any]], a: float, b: float) -> Optional[str]:
    cover: Dict[str, float] = {}
    for s in truth:
        overlap = min(b, s["end"]) - max(a, s["start"])
        if overlap > 0:
            cover[s["label"]] = cover.get(s["label"], 0.0) + overlap
    return max(cover, key=lambda k: cover[k]) if cover else None


def segment_outcomes(
    truth: Sequence[Dict[str, Any]], est: Sequence[Dict[str, Any]], start: float, end: float
) -> List[Tuple[float, bool]]:
    """``(confidence, correct)`` for each labelled segment that overlaps the content range."""
    out = []
    for s in est:
        a, b = max(s["start"], start), min(s["end"], end)
        if b > a:
            out.append((s["confidence"], _majority(truth, a, b) == s["label"]))
    return out
