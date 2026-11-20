"""Content edges and fades from a frame-wise RMS curve (pure, numpy only).

Silence is "far below the track's own loud level", not "below a fixed level", so a quiet intro is
content and a hissy lead-in is still found. A fade is a ramp that reaches a plateau: the model is
a linear ramp from the content edge to the plateau level, fitted by least squares, and accepted
only when it fits and is not just an abrupt jump or a flat start.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

SILENCE_DB = -55.0  # below the 95th-percentile level
ABS_FLOOR = 1.5e-4  # never call anything above this absolute RMS silent
MIN_FADE_S = 1.5
MAX_FADE_S = 30.0
PLATEAU_S = 1.0
START_MAX = 0.15  # the level at the content edge, as a share of the plateau
FIT_ERR_MAX = 0.05  # relative squared error of the ramp-then-plateau model


def content_edges(rms: np.ndarray, hop_s: float) -> Tuple[float, float]:
    """``(start_s, end_s)`` of the audible content; the whole file if nothing is quiet."""
    if rms.size == 0:
        return 0.0, 0.0
    level = float(np.percentile(rms, 95))
    thr = max(level * 10 ** (SILENCE_DB / 20.0), ABS_FLOOR)
    active = np.flatnonzero(rms > thr)
    if active.size == 0:
        return 0.0, rms.size * hop_s
    return float(active[0]) * hop_s, float(active[-1] + 1) * hop_s


def smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or x.size < width:
        return x
    kernel = np.ones(width) / width
    return np.convolve(
        np.pad(x, (width // 2, width - 1 - width // 2), mode="edge"), kernel, "valid"
    )


def _ramp_fit(r: np.ndarray, hop_s: float) -> Optional[Tuple[float, float]]:
    """``(ramp length in s, relative error)`` of the best ramp-then-plateau fit to ``r``."""
    n_plateau = int(PLATEAU_S / hop_s)
    best: Optional[Tuple[float, float]] = None
    for length in np.arange(MIN_FADE_S, min(MAX_FADE_S, r.size * hop_s * 0.45), 0.1):
        n = int(length / hop_s)
        seg = r[: n + n_plateau]
        if seg.size < n + n_plateau // 2:
            break
        ref = float(np.median(r[n : n + n_plateau]))
        if ref <= 1e-9:
            continue
        if float(np.median(r[: max(1, int(0.3 / hop_s))])) > START_MAX * ref:
            continue  # a fade starts from silence; an intro that is merely quieter is not one
        model = np.minimum(1.0, np.arange(seg.size) / max(n, 1)) * ref
        err = float(np.mean((seg - model) ** 2)) / ref**2
        if best is None or err < best[1]:
            best = (float(length), err)
    return best


def fade_in(rms: np.ndarray, hop_s: float, start_s: float) -> Optional[Tuple[float, float, float]]:
    """``(start_s, end_s, confidence)`` of a fade-in beginning at ``start_s``, else ``None``."""
    r = smooth(rms, max(1, int(0.5 / hop_s)))[int(start_s / hop_s) :]
    fit = _ramp_fit(r, hop_s)
    if fit is None or fit[1] > FIT_ERR_MAX:
        return None
    return start_s, start_s + fit[0], round(max(0.3, 0.9 - 4.0 * fit[1]), 3)


def fade_out(rms: np.ndarray, hop_s: float, end_s: float) -> Optional[Tuple[float, float, float]]:
    """``(start_s, end_s, confidence)`` of a fade-out ending at ``end_s``, else ``None``."""
    r = smooth(rms, max(1, int(0.5 / hop_s)))[: int(end_s / hop_s)][::-1]
    fit = _ramp_fit(r, hop_s)
    if fit is None or fit[1] > FIT_ERR_MAX:
        return None
    return end_s - fit[0], end_s, round(max(0.3, 0.9 - 4.0 * fit[1]), 3)
