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
# On a track whose loudness barely varies, the strongest novelty is a timbre change at the file's
# ends (intro and outro), so a share of it hides the quieter changes between verses and choruses.
# There a peak needs only PEAK_REL_LOW of the strongest one (the absolute floor and the merge step
# of ``merge_calc`` still apply). Contrast is measured by ``energy_contrast``; on the synthetic
# corpus the steady specs measure 0.15 to 0.17, the nearest other fixture (oversplit-100, which the
# lower share would cut into one part too many) 0.197, and tracks with a drop 0.35 or more.
PEAK_REL_LOW = 0.15
LOW_CONTRAST = 0.18
CONTRAST_BLOCK_S = 4.0


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


def energy_contrast(energy: np.ndarray, step_s: float, start_s: float, end_s: float) -> float:
    """The spread (90th minus 10th percentile) of the mean energy per ``CONTRAST_BLOCK_S`` block
    inside ``start_s`` to ``end_s``; 0.0 when the range holds less than two blocks."""
    k = max(1, int(round(CONTRAST_BLOCK_S / step_s)))
    part = np.asarray(energy, dtype=float)[int(start_s / step_s) : int(end_s / step_s)]
    n = part.size // k
    if n < 2:
        return 0.0
    blocks = part[: n * k].reshape(n, k).mean(axis=1)
    return float(np.percentile(blocks, 90) - np.percentile(blocks, 10))


def peak_rel(contrast: float) -> float:
    """The share of the strongest novelty a peak must reach, lower when the energy is steady."""
    return PEAK_REL_LOW if contrast < LOW_CONTRAST else PEAK_REL


def pick_peaks(
    nov: np.ndarray, step_s: float, gap_s: float = MIN_GAP_S, rel: float = PEAK_REL
) -> List[int]:
    """Frame indices of the novelty peaks that clear both thresholds, strongest first kept."""
    if nov.size < 3:
        return []
    floor = max(rel * float(nov.max()), PEAK_ABS)
    cand = [i for i in range(1, nov.size - 1) if nov[i] >= floor and nov[i] >= nov[i - 1]]
    cand = [i for i in cand if nov[i] > nov[i + 1]]
    cand.sort(key=lambda i: -nov[i])
    kept: List[int] = []
    for i in cand:
        if all(abs(i - j) * step_s >= gap_s for j in kept):
            kept.append(i)
    return sorted(kept)
