"""Pure helpers on 1-D energy signals (numpy only, no I/O)."""

from __future__ import annotations

from typing import List

import numpy as np


def tempo_candidates(bpm: float) -> List[float]:
    out = set()
    for mult in (0.5, 1.0, 2.0):
        v = bpm * mult
        if 40 <= v <= 240:
            out.add(round(v, 1))
    return sorted(out)


def norm95(x: np.ndarray) -> np.ndarray:
    """Normalize by the 95th percentile (clipped to 1) so one transient can't flatten it."""
    if x.size == 0:
        return x
    p95 = float(np.percentile(x, 95))
    if p95 < 1e-9:
        p95 = 1.0
    return np.clip(x / p95, 0.0, 1.0)


def snap(ts: float, arr: np.ndarray) -> float:
    if arr.size == 0:
        return round(float(ts), 3)
    j = int(np.argmin(np.abs(arr - ts)))
    return round(float(arr[j]), 3)


def rising_edges(
    e: np.ndarray, grid: np.ndarray, hi: float, lo: float, min_gap: float = 8.0
) -> List[float]:
    """Times where a (0-1) energy signal jumps from below `lo` to above `hi`."""
    es = np.convolve(e, np.ones(3) / 3.0, mode="same")
    out: List[float] = []
    for i in range(2, len(es)):
        if es[i] > hi and es[i - 2] < lo and (not out or grid[i] - out[-1] > min_gap):
            out.append(float(grid[i]))
    return out


def quiet_region_edges(
    grid: np.ndarray, e: np.ndarray, thresh: float = 0.15, min_len: float = 3.0
) -> List[float]:
    """Start/end times of low-energy runs — catches empty intros/breakdowns even
    where the beat tracker finds no beats (so beat-based segmentation can't resolve them)."""
    edges: List[float] = []
    quiet = e < thresh
    n = len(quiet)
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            start_t, end_t = float(grid[i]), float(grid[j - 1])
            if end_t - start_t >= min_len:
                edges += [start_t, end_t]
            i = j
        else:
            i += 1
    return edges


def bin_frames(x: np.ndarray, frame_s: float, step_s: float) -> "tuple[float, np.ndarray]":
    """Mean of ``x`` over bins of about ``step_s``: ``(actual step in s, binned values)``."""
    k = max(1, int(round(step_s / frame_s)))
    n = x.size // k
    return k * frame_s, x[: n * k].reshape(n, k).mean(axis=1) if n else x[:0]
