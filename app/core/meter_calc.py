"""Meter (3 or 4 beats per bar) and downbeats from tracked beats and anchors (pure).

Two cues, both ``heuristic``:

* **Meter**, from the accent. A bar of ``m`` beats shows as a repeating accent: the onset strength
  at the beats differs by position in the bar more than chance explains. Positions are counted
  from the anchors (below) and from the first beat, and the share of the strength variance that
  position explains is computed for 3 and 4 (adjusted for the number of groups, then divided by
  its spread under no accent, a z-score). A meter is claimed only when its z-score is at least
  ``MIN_Z`` and it beats the other meter by ``MIN_MARGIN``; otherwise nothing is claimed (the
  meter is never assumed to be 4/4). 2 and 6 are the same accent pattern as 4 and 3.
* **Downbeats**, from the anchors: the start of the music and the detected drops. They are the
  places where a bar starts in dance music, so from each anchor the grid steps one bar (``m``
  beats) at a time, each step snapped to a tracked beat, until the next anchor restarts the grid
  (which absorbs a beat the tracker skipped or added, for example across a breakdown). A music
  that starts on a pickup, or a drop that is not on a downbeat, shifts the bars: the bar
  confidence falls when the anchors do not agree with the grid.

No confidence exceeds ``CAP`` until held-out evidence exists (``GATE_PASSED``, spotdown#20).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

METERS = (3, 4)
MIN_BEATS = 24  # at least 6 bars of 4 beats: fewer and the accent is not told from chance
GATE_PASSED = False  # held-out evidence for raising the cap does not exist yet (spotdown#20)
CAP = 0.4  # the most confidence a meter or a downbeat carries before held-out evidence exists
MIN_Z = 3.0  # the explained variance, in spreads of its distribution when there is no accent
MIN_MARGIN = 0.02  # how much the chosen meter must explain more than the other
SNAP = 0.3  # a bar step snaps to a tracked beat within this fraction of a beat
AGREE_TOL = 0.15  # an anchor this close (in bars) to a whole number of bars agrees with the grid


def _clip(x: float) -> float:
    return min(1.0, max(0.0, x))


def explained(values: Sequence[float], positions: Sequence[int], m: int) -> float:
    """Adjusted share of the variance of ``values`` that the position in the bar explains."""
    n = len(values)
    grand = sum(values) / n
    total = sum((v - grand) ** 2 for v in values)
    if total <= 1e-9 * (grand * grand * n + 1e-12) or n <= m:  # flat: no accent at all
        return 0.0
    between = 0.0
    for j in range(m):
        group = [v for v, p in zip(values, positions) if p == j]
        if group:
            between += len(group) * (sum(group) / len(group) - grand) ** 2
    return 1.0 - (1.0 - between / total) * (n - 1) / (n - m)


def _z(adjusted: float, n: int, m: int) -> float:
    """The explained variance in spreads of its distribution under no accent (about
    ``sqrt(2 (m - 1)) / n``)."""
    return adjusted * n / math.sqrt(2.0 * (m - 1))


def _positions(
    beats: Sequence[float], anchors: Sequence[float], beat_s: float, m: int
) -> List[int]:
    """The position in the bar of each beat, counted in beats from the latest anchor before it
    (from the first anchor for the beats before it); the plain beat index is used when there is
    no anchor."""
    out: List[int] = []
    for t in beats:
        before = [a for a in anchors if a <= t + SNAP * beat_s]
        ref = before[-1] if before else (anchors[0] if anchors else beats[0])
        out.append(int(round((t - ref) / beat_s)) % m)
    return out


def _bars(
    beats: Sequence[float], anchors: Sequence[float], beat_s: float, m: int, decided: float
) -> List[Dict[str, Any]]:
    bar_s = m * beat_s
    out: List[Dict[str, Any]] = []
    for i, a in enumerate(anchors):
        nxt = anchors[i + 1] if i + 1 < len(anchors) else None
        end = nxt if nxt is not None else beats[-1] + beat_s / 2
        if nxt is not None:  # do the anchors agree with a whole number of bars?
            off = abs((nxt - a) / bar_s - round((nxt - a) / bar_s))
            agree = 1.0 if off <= AGREE_TOL else 0.5
        else:
            agree = 0.8  # nothing after it contradicts the grid, nothing confirms it
        t = a
        while t < end - SNAP * beat_s:
            near = min(beats, key=lambda b: abs(b - t))
            at = near if abs(near - t) < SNAP * beat_s else t
            if beats[0] - beat_s / 2 <= at <= beats[-1] + beat_s / 2:
                out.append(
                    {"start": round(at, 3), "confidence": round(min(CAP, decided * agree), 3)}
                )
            t = at + bar_s
    return out


def estimate(
    beats: Sequence[float], strengths: Sequence[float], anchors: Sequence[float]
) -> Optional[Dict[str, Any]]:
    """``{"beats_per_bar", "confidence", "bars": [{"start", "confidence"}]}`` or None.

    ``beats`` are the tracked beat times, ``strengths`` the onset strength at each, ``anchors``
    the times where a bar is known to start (the content start and the drops), in any order."""
    if len(beats) < MIN_BEATS or len(strengths) != len(beats) or not anchors:
        return None
    gaps = sorted(b - a for a, b in zip(beats[:-1], beats[1:]))
    beat_s = gaps[len(gaps) // 2]
    if beat_s <= 0:
        return None
    anchored: List[float] = []
    for a in sorted(anchors):  # anchors within half a beat of each other are one anchor
        if not anchored or a - anchored[-1] > beat_s / 2:
            anchored.append(float(a))
    scores: Dict[int, float] = {}
    for m in METERS:
        plain = explained(strengths, [i % m for i in range(len(beats))], m)
        from_anchor = explained(strengths, _positions(beats, anchored, beat_s, m), m)
        scores[m] = max(plain, from_anchor)
    # on a tie the shorter bar wins (3 before 4): a 4-beat bar is claimed only on evidence
    best = max(METERS, key=lambda m: (scores[m], -m))
    other = max(s for m, s in scores.items() if m != best)
    if _z(scores[best], len(beats), best) < MIN_Z or scores[best] - other < MIN_MARGIN:
        return None
    decided = _clip(scores[best]) * _clip((scores[best] - other) / max(scores[best], 1e-9))
    return {
        "beats_per_bar": best,
        "confidence": round(min(CAP, decided), 3),
        "bars": _bars(beats, anchored, beat_s, best, decided),
    }
