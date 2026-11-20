"""Phrases of 8 or 16 bars from section boundaries on the bar grid (pure).

A phrase is a run of bars between two structural boundaries (the segment starts the structure
analysis found, which include the detected drops). Starting from the first boundary, the next
boundary that lies 8 bars later closes an 8-bar phrase, else one 16 bars later closes a 16-bar
phrase, else the chain moves on to the next boundary, so sections that are not a whole phrase
long produce nothing rather than a guess.

The bar length is four beats of the estimated tempo: the meter is not estimated here (and the
document does not claim one), so every phrase is a ``heuristic`` that assumes 4/4. Until held-out
evidence exists (spotdown#20) no confidence exceeds ``CAP``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

PHRASE_BARS = (8, 16)
LENGTH_TOL_BARS = 0.5  # a boundary this close to a whole number of bars counts as on it
MIN_BAR_S = 0.4
CAP = 0.4  # the most confidence a phrase carries before held-out evidence exists
SCALE = 0.5  # of the mean boundary confidence


def _snap(t: float, bar_starts: Sequence[float], bar_s: float) -> float:
    near = min(bar_starts, key=lambda b: abs(b - t), default=t)
    return round(near if abs(near - t) <= bar_s / 2 else t, 3)


def phrases(
    candidates: Sequence[Tuple[float, float]],
    bar_s: float,
    bar_starts: Sequence[float],
) -> List[Dict[str, Any]]:
    """``[{"start", "end", "bars", "confidence"}]`` from ``(time, confidence)`` boundaries.

    ``candidates`` are the structural boundaries inside the content (time in seconds and the
    boundary confidence), ``bar_s`` the bar length and ``bar_starts`` the tracked bar starts.
    """
    if bar_s < MIN_BAR_S:
        return []
    cands = sorted(candidates)
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(cands):
        found = None
        for bars in PHRASE_BARS:
            for j in range(i + 1, len(cands)):
                if abs((cands[j][0] - cands[i][0]) / bar_s - bars) <= LENGTH_TOL_BARS:
                    found = (j, bars)
                    break
            if found:
                break
        if found is None:
            i += 1
            continue
        j, bars = found
        conf = min(CAP, SCALE * (cands[i][1] + cands[j][1]) / 2)
        out.append(
            {
                "start": _snap(cands[i][0], bar_starts, bar_s),
                "end": _snap(cands[j][0], bar_starts, bar_s),
                "bars": bars,
                "confidence": round(conf, 3),
            }
        )
        i = j
    return out
