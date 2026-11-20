"""mir_eval-style metrics on plain lists of seconds (pure, numpy only, no I/O).

The definitions follow mir_eval's: beat F-measure with a +-70 ms window after trimming beats before
5 s; boundary hit-rate F with a window on the interior boundaries; one-to-one nearest matching.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

BEAT_TRIM_S = 5.0


def match_count(ref: Sequence[float], est: Sequence[float], window_s: float) -> int:
    """The size of a one-to-one matching of ``est`` to ``ref`` within ``window_s`` (greedy by
    distance, which is optimal for points on a line when the window is no wider than half the
    smallest gap; close enough for scoring)."""
    pairs = sorted((abs(r - e), i, j) for i, r in enumerate(ref) for j, e in enumerate(est))
    used_r, used_e, hits = set(), set(), 0
    for dist, i, j in pairs:
        if dist > window_s:
            break
        if i in used_r or j in used_e:
            continue
        used_r.add(i)
        used_e.add(j)
        hits += 1
    return hits


def f_measure(precision: float, recall: float) -> float:
    return 0.0 if precision + recall <= 0 else 2.0 * precision * recall / (precision + recall)


def prf(ref: Sequence[float], est: Sequence[float], window_s: float) -> Tuple[float, float, float]:
    """``(precision, recall, F)``; two empty lists score 1, one empty list scores 0."""
    if not ref and not est:
        return 1.0, 1.0, 1.0
    if not ref or not est:
        return 0.0, 0.0, 0.0
    hits = match_count(ref, est, window_s)
    p, r = hits / len(est), hits / len(ref)
    return p, r, f_measure(p, r)


def beat_f(ref: Sequence[float], est: Sequence[float], window_s: float = 0.07) -> float:
    ref_t = [t for t in ref if t >= BEAT_TRIM_S]
    est_t = [t for t in est if t >= BEAT_TRIM_S]
    return prf(ref_t, est_t, window_s)[2]


def interior(times: Sequence[float], duration_s: float, margin_s: float = 0.5) -> List[float]:
    """Boundaries away from the file's two ends (those are given, not detected)."""
    return [t for t in times if margin_s < t < duration_s - margin_s]


def boundary_f(
    ref: Sequence[float], est: Sequence[float], duration_s: float, window_s: float = 3.0
) -> float:
    return prf(interior(ref, duration_s), interior(est, duration_s), window_s)[2]


def bpm_errors(true_bpm: float, est_bpm: float) -> Tuple[bool, bool]:
    """``(within tolerance at some octave, within tolerance at the exact octave)`` for +-2 %."""
    if true_bpm <= 0 or est_bpm <= 0:
        return False, False
    exact = abs(est_bpm - true_bpm) / true_bpm <= 0.02
    octave = any(abs(est_bpm * f - true_bpm) / true_bpm <= 0.02 for f in (0.5, 1.0, 2.0))
    return octave, exact


def edge_hits(ref: Sequence[float], est: Sequence[float], window_s: float = 0.25) -> List[bool]:
    """For each reference edge, whether the estimate has one within ``window_s``."""
    return [any(abs(r - e) <= window_s for e in est) for r in ref]


def drop_counts(
    ref: Sequence[float], est: Sequence[float], window_s: float
) -> Tuple[int, int, int]:
    """``(true positives, false positives, false negatives)`` of drops within ``window_s``."""
    hits = match_count(ref, est, window_s)
    return hits, len(est) - hits, len(ref) - hits


def ece(confidences: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error: the bin-weighted gap between confidence and accuracy."""
    n = len(confidences)
    if n == 0:
        return 0.0
    conf = np.clip(np.asarray(confidences, dtype=float), 0.0, 1.0)
    ok = np.asarray(correct, dtype=float)
    idx = np.minimum((conf * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            total += float(mask.sum()) / n * abs(float(ok[mask].mean()) - float(conf[mask].mean()))
    return total


def percentile(values: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q)) if len(values) else 0.0
