"""Merge over-fine segments (pure, no I/O): the step between the cut boundaries and the labels.

The novelty peaks cut the track into segments; a vocal song with fills or a new timbre inside a
verse gets extra boundaries there. Two passes remove a boundary, weakest novelty first (ties by
time, so the result is deterministic). ``segments.segments`` runs them in this order:

1. ``merge_similar``: two neighbours that sound the same (cosine similarity of their mean
   features at least ``SIMILAR_COS``) whose boundary has novelty below ``WEAK_NOVELTY`` are one
   section split by a variation. This is the repetition test applied to the pair itself: the
   coarse clusters of ``segments.cluster_groups`` (four at most) put a verse and a chorus
   together. On the synthetic corpus the neighbours of a real boundary score 0.3 or less and
   those of a variation 0.77 or more, so the constant sits in the gap. It runs first, on the
   segments as cut, so that merging short pieces cannot blur the features it compares.
2. ``merge_short``: a segment shorter than ``SHORT_BARS`` bars at the detected tempo
   (``SHORT_S`` seconds when there is no tempo) is merged into the neighbour across its weaker
   boundary, unless that boundary is a strong one (novelty ``>= STRONG_NOVELTY``: a short but
   clearly separate section stays). A short segment whose other end is a protected boundary (it
   sits beside a drop or an edge) is merged only across a weak boundary (below
   ``WEAK_NOVELTY``): a short piece before a drop or the end is more likely real than a
   variation. A real 4-bar section is cut at 3 to 4.3 bars (a boundary found 2 s early shortens
   it), so the limit is below 3; 2.5 keeps every 4-bar section of the synthetic corpus and
   removes the 1 and 2 bar slivers.

The constants (0.7, 0.4, 0.6, 2.5 bars, 6 s) are fitted on the synthetic corpus only. They are
not validated on real music: that waits for held-out evidence (spotdown#20).

A boundary is never removed when it is the one standing for a protected time (a detected drop, a
content edge or a quiet-region edge): the boundary nearest to each protected time within
``near_s`` is kept, so drop times stay exact.
"""

from __future__ import annotations

from typing import Callable, List, Sequence

import numpy as np

SHORT_BARS = 2.5
SHORT_S = 6.0
STRONG_NOVELTY = 0.6
WEAK_NOVELTY = 0.4
SIMILAR_COS = 0.7


def protected_times(
    times: Sequence[float], protected: Sequence[float], near_s: float
) -> List[float]:
    """The interior boundaries that stand for a protected time (the nearest within ``near_s``)."""
    keep: List[float] = []
    for p in protected:
        best = min(range(1, len(times) - 1), key=lambda i: abs(times[i] - p), default=None)
        if best is not None and abs(times[best] - p) <= near_s:
            keep.append(times[best])
    return keep


def _remove_weakest(
    times: List[float],
    novelty: List[float],
    keep: Sequence[float],
    rule: Callable[[int], bool],
) -> bool:
    """Remove the weakest boundary ``rule`` accepts (``times``/``novelty`` change in place)."""
    best = None
    for i in range(1, len(times) - 1):  # the boundary between segment i - 1 and segment i
        if times[i] not in keep and rule(i) and (best is None or novelty[i] < novelty[best]):
            best = i
    if best is None:
        return False
    del times[best], novelty[best]
    return True


def merge_short(
    times: Sequence[float], novelty: Sequence[float], keep: Sequence[float], bar_s: float
) -> "tuple[List[float], List[float]]":
    """``times`` holds each segment's start then the end of the last; ``novelty[i]`` is the
    boundary at the start of segment ``i``. Returns both lists after the short segments merge."""
    short_s = SHORT_BARS * bar_s if bar_s > 0 else SHORT_S
    t, nov = list(times), list(novelty)

    def short(i: int) -> bool:
        left, right = t[i] - t[i - 1], t[i + 1] - t[i]
        if min(left, right) >= short_s or nov[i] >= STRONG_NOVELTY:
            return False
        tied = (t[i - 1] if left < right else t[i + 1]) in keep  # the short one's other end
        return nov[i] < WEAK_NOVELTY or not tied

    while _remove_weakest(t, nov, keep, short):
        pass
    return t, nov


def merge_similar(
    times: Sequence[float],
    novelty: Sequence[float],
    feats: Sequence[np.ndarray],
    keep: Sequence[float],
) -> "tuple[List[float], List[float]]":
    """``feats[i]`` is the mean feature vector of segment ``i``. A merged segment's vector
    is the duration-weighted mean of its parts."""
    t, nov, f = list(times), list(novelty), list(feats)

    def similar(i: int) -> bool:
        return nov[i] < WEAK_NOVELTY and _cosine(f[i - 1], f[i]) >= SIMILAR_COS

    while True:
        before = list(t)
        if not _remove_weakest(t, nov, keep, similar):
            return t, nov
        k = next(i for i in range(len(before)) if i >= len(t) or before[i] != t[i])
        d1, d2 = before[k] - before[k - 1], before[k + 1] - before[k]
        f[k - 1] = (f[k - 1] * d1 + f[k] * d2) / (d1 + d2)
        del f[k]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))
