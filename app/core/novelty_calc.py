"""Structural novelty from a feature matrix (pure, numpy only): Foote's checkerboard method.

Features are z-scored per dimension, compared by cosine into a self-similarity matrix, and a
Gaussian-tapered checkerboard kernel slid along the diagonal scores how much the music before a
frame differs from the music after it. Peaks are section boundaries.
"""

from __future__ import annotations

from typing import List

import numpy as np

HALF_WIDTH = 8  # frames either side of a boundary the kernel looks at (4 s at 0.5 s frames)
PEAK_REL = 0.3  # a peak must reach this share of the track's strongest novelty ...
PEAK_ABS = 0.08  # ... and this absolute value, so a flat track yields no boundaries
MIN_GAP_S = 6.0


def zscore(feat: np.ndarray) -> np.ndarray:
    return (feat - feat.mean(axis=1, keepdims=True)) / (feat.std(axis=1, keepdims=True) + 1e-6)


def similarity(feat: np.ndarray) -> np.ndarray:
    unit = feat / (np.linalg.norm(feat, axis=0, keepdims=True) + 1e-9)
    return unit.T @ unit


def kernel(half: int = HALF_WIDTH) -> np.ndarray:
    pos = np.arange(-half, half) + 0.5
    g = np.exp(-0.5 * (pos / (half / 2.0)) ** 2)
    k = np.outer(g, g) * np.sign(np.outer(pos, pos))
    return k / np.abs(k).sum()


def novelty_curve(feat: np.ndarray, half: int = HALF_WIDTH) -> np.ndarray:
    """Novelty per frame, scaled so that a clean full-contrast boundary scores about 1."""
    sim = np.pad(similarity(zscore(feat)), half, mode="edge")
    k = kernel(half)
    n = feat.shape[1]
    return np.array([float((sim[i : i + 2 * half, i : i + 2 * half] * k).sum()) for i in range(n)])


def pick_peaks(nov: np.ndarray, step_s: float, gap_s: float = MIN_GAP_S) -> List[int]:
    """Frame indices of the novelty peaks that clear both thresholds, strongest first kept."""
    if nov.size < 3:
        return []
    floor = max(PEAK_REL * float(nov.max()), PEAK_ABS)
    cand = [i for i in range(1, nov.size - 1) if nov[i] >= floor and nov[i] >= nov[i - 1]]
    cand = [i for i in cand if nov[i] > nov[i + 1]]
    cand.sort(key=lambda i: -nov[i])
    kept: List[int] = []
    for i in cand:
        if all(abs(i - j) * step_s >= gap_s for j in kept):
            kept.append(i)
    return sorted(kept)
