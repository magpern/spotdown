"""The structure-derived groups of an analysis-v1 document: segments, labels and phrases. Pure.

Everything here reads the core analyser's segments (and drops, bars, tempo) and nothing else.
Labels and phrases are ``heuristic`` and capped at 0.4 until held-out evidence exists
(``label_calc.GATE_PASSED``, ``phrase_calc.CAP``; spotdown#20).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ..core import label_calc, phrase_calc
from . import meter_doc

END_CONFIDENCE = 0.5  # the content end is a measured edge, not a novelty peak


def clip(x: float) -> float:
    return round(min(1.0, max(0.0, float(x))), 3)


NOVELTY_SCALE = 0.08  # the novelty at which a boundary is trusted about 63 % (calibration)


def boundary_confidence(novelty: Optional[float], first: bool) -> float:
    """From the self-similarity novelty at the boundary: a saturating curve, capped below 1.
    The first segment starts the file, so nothing is claimed about it beyond a measured start."""
    if first or novelty is None:
        return 0.9 if first else 0.3
    return clip(0.95 * (1.0 - math.exp(-max(0.0, novelty) / NOVELTY_SCALE)))


def structure(core: Dict[str, Any]) -> Dict[str, Any]:
    segments, groups = [], {}
    for i, seg in enumerate(core["segments"]):
        sid = f"s{i + 1}"
        segments.append(
            {
                "id": sid,
                "start_s": seg["start"],
                "end_s": seg["end"],
                "boundary_confidence": boundary_confidence(seg.get("boundary_novelty"), i == 0),
                "method": "estimated",
            }
        )
        groups.setdefault(seg.get("group", 0), []).append(sid)
    repetition = [
        {"segment_ids": ids, "confidence": 0.5, "method": "estimated"}
        for _, ids in sorted(groups.items())
        if len(ids) >= 2
    ]
    return {"segments": segments, "repetition_groups": repetition}


def labels(core: Dict[str, Any]) -> List[Dict[str, Any]]:
    found = label_calc.label_segments(
        core["segments"], core["drops"], core["content_start_s"], core["content_end_s"]
    )
    return [
        {
            "segment_id": f"s{x['index'] + 1}",
            "label": x["label"],
            "confidence": clip(x["confidence"]),
            "method": "heuristic",
        }
        for x in found
    ]


def phrases(core: Dict[str, Any]) -> List[Dict[str, Any]]:
    """8 and 16 bar phrases between structural boundaries (heuristic; 4/4 bars, none for a 3/4
    meter claim: 3/4 phrases were overconfident on the only evidence, 0.090 against 0.03)."""
    if meter_doc.beats_per_bar(core) != 4:
        return []  # phrases are made of 4/4 bars: a 3/4 claim withholds them (see quality-harness)
    segs = core["segments"]
    inside = label_calc.content_indices(segs, core["content_start_s"], core["content_end_s"])
    cands = [
        (
            max(segs[i]["start"], core["content_start_s"]),
            boundary_confidence(segs[i].get("boundary_novelty"), n == 0),
        )
        for n, i in enumerate(inside)
    ]
    end = core["content_end_s"]
    bar_s = meter_doc.bar_seconds(core)
    if cands and end - cands[-1][0] > bar_s / 2:
        cands.append((end, END_CONFIDENCE))
    found = phrase_calc.phrases(cands, bar_s, [b["start"] for b in core.get("bars", [])])
    return [
        {
            "start_s": p["start"],
            "end_s": p["end"],
            "bars": p["bars"],
            "confidence": clip(p["confidence"]),
            "method": "heuristic",
        }
        for p in found
    ]
