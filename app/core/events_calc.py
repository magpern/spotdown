"""Drops, breakdowns and build-ups from energy curves (pure, numpy only).

The detector is a step finder: the novelty of a curve at ``t`` is the mean of the next ``WINDOW_S``
minus the mean of the previous ``WINDOW_S``. A drop is a strong positive peak of the low-band or
percussive novelty, a breakdown a strong negative one; times are snapped to the beat grid.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np

WINDOW_S = 3.0
DROP_MIN = 0.3
BREAKDOWN_MIN = 0.3
MIN_GAP_S = 8.0
PERC_WEIGHT = 0.9


def novelty(x: np.ndarray, step_s: float, window_s: float = WINDOW_S) -> np.ndarray:
    """Mean of the next window minus mean of the previous window, per sample."""
    w = max(1, int(round(window_s / step_s)))
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out = np.zeros(x.size)
    for i in range(w, x.size - w + 1):
        out[i] = (cs[i + w] - cs[i]) / w - (cs[i] - cs[i - w]) / w
    return out


def pick_peaks(d: np.ndarray, step_s: float, floor: float, gap_s: float = MIN_GAP_S) -> List[int]:
    """Indices of local maxima of ``d`` above ``floor``, strongest first kept, ``gap_s`` apart."""
    cand = [
        i for i in range(1, d.size - 1) if d[i] >= floor and d[i] >= d[i - 1] and d[i] > d[i + 1]
    ]
    cand.sort(key=lambda i: -d[i])
    kept: List[int] = []
    for i in cand:
        if all(abs(i - j) * step_s >= gap_s for j in kept):
            kept.append(i)
    return sorted(kept)


def snap_to_grid(t: float, bpm: float, phase_s: float) -> float:
    period = 60.0 / bpm
    return phase_s + round((t - phase_s) / period) * period


def detect(
    low: np.ndarray, perc: np.ndarray, step_s: float, bpm: float, phase_s: float
) -> Dict[str, List[Dict[str, Any]]]:
    """``{"drops": [...], "breakdowns": [...]}``; each item has ``t``, ``strength`` (0-1)."""
    n = min(low.size, perc.size)
    d = np.maximum(novelty(low[:n], step_s), PERC_WEIGHT * novelty(perc[:n], step_s))
    out: Dict[str, List[Dict[str, Any]]] = {"drops": [], "breakdowns": []}
    for key, signal, floor in (("drops", d, DROP_MIN), ("breakdowns", -d, BREAKDOWN_MIN)):
        for i in pick_peaks(signal, step_s, floor):
            t = float(i) * step_s
            if bpm > 0:
                t = max(0.0, snap_to_grid(t, bpm, phase_s))
            out[key].append({"t": round(t, 3), "strength": round(float(min(1.0, signal[i])), 3)})
    return out


def build_ups(
    energy: "Sequence[float] | np.ndarray", step_s: float, drops: Sequence[float]
) -> List[Dict[str, float]]:
    """The sustained climb of ``energy`` right before each drop (at least 3 s, at most 16 s)."""
    es = np.convolve(np.asarray(energy, dtype=float), np.ones(3) / 3.0, mode="same")
    found = []
    for d in drops:
        di = int(min(es.size - 1, round(d / step_s)))
        s = di
        while s > 0 and es[s] >= es[s - 1] - 0.02 and (di - s) * step_s < 16:
            s -= 1
        if (di - s) * step_s >= 3:
            found.append({"start": round(s * step_s, 3), "end": round(d, 3)})
    return found
