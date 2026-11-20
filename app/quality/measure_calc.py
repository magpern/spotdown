"""Time and memory from real runs (pure): only tracks near five minutes count, never a scaling.

The synthetic run extrapolates the time of 40 to 110 s fixtures to five minutes and measures
memory on short tracks. For an external corpus a gate is measured on the tracks whose length is in
the rule's window (``min_track_s`` to ``max_track_s``) and is *not measured* (``None``) with fewer
than ``min_tracks`` of them, so a corpus of short clips can never pass a five-minute gate. The
seconds are the job's decode and analysis time as measured; the length factor only normalises a
track a little under or over five minutes. Memory is the peak of the analysis child of each track
(one isolated job per track), the maximum over the same tracks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

FIVE_MIN_S = 300.0


def _p95(values: Sequence[float]) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), 95))


def long_tracks(results: Sequence[Dict[str, Any]], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        r
        for r in results
        if r["ok"] and rule["min_track_s"] <= (r["duration_s"] or 0.0) <= rule["max_track_s"]
    ]


def measure(results: Sequence[Dict[str, Any]], rule: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """``p95_seconds_per_5min`` and ``peak_memory_gib`` over the five-minute tracks (None when
    too few were run or memory was not measured), plus the memory peak over every track (the mix
    gate: a mix is not five minutes)."""
    window = long_tracks(results, rule)
    enough = len(window) >= rule["min_tracks"]
    mem = [r["peak_gib"] for r in window if r.get("peak_gib") is not None]
    done = [r for r in results if r["ok"]]
    all_mem = [r["peak_gib"] for r in done if r.get("peak_gib") is not None]
    return {
        "long_tracks": float(len(window)),
        "p95_seconds_per_5min": round(
            _p95([r["work_s"] * FIVE_MIN_S / r["duration_s"] for r in window]), 2
        )
        if enough
        else None,
        "peak_memory_gib": round(max(mem), 3) if enough and len(mem) == len(window) else None,
        "max_track_seconds": round(max(r["work_s"] for r in done), 2) if done else None,
        "peak_memory_gib_all": round(max(all_mem), 3)
        if done and len(all_mem) == len(done)
        else None,
    }
