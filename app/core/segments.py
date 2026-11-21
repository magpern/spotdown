"""Structural segmentation on the file's own clock: novelty peaks, drops and silence edges.

Boundaries come from a self-similarity novelty curve over log-mel and chroma features (see
``novelty_calc``), plus the detected drops and the content edges; none of them needs a beat grid,
so empty intros and breakdowns resolve like any other section.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import librosa
import numpy as np

from . import HOP, novelty_calc
from .signal_calc import quiet_region_edges

MIN_SEG_S = 2.0
FRAME_S = 0.5
MERGE_S = 3.0  # a drop or edge this close to a novelty peak is the same boundary


def cluster_groups(seg_feats: List[np.ndarray]) -> List[int]:
    """Group structurally-similar segments (so repeats share a group id)."""
    if len(seg_feats) < 2:
        return [0] * len(seg_feats)
    try:
        from scipy.cluster.hierarchy import fcluster, linkage

        Z = linkage(np.vstack(seg_feats), method="ward")
        labels = fcluster(Z, t=min(4, len(seg_feats)), criterion="maxclust")
        return [int(v) for v in labels]
    except Exception:
        return [0] * len(seg_feats)


def features(y: np.ndarray, sr: int) -> "tuple[np.ndarray, float]":
    """Log-mel plus half-weight chroma, averaged to ``FRAME_S`` frames: ``(matrix, frame_s)``."""
    mel = librosa.power_to_db(
        librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=40, fmax=8000)
    )
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=HOP)
    k = max(1, int(round(FRAME_S / (HOP / sr))))
    n = mel.shape[1] // k

    def avg(a: np.ndarray) -> np.ndarray:
        return a[:, : n * k].reshape(a.shape[0], n, k).mean(axis=2)

    z = novelty_calc.zscore
    return np.vstack([z(avg(mel)), 0.5 * z(avg(chroma))]), k * HOP / sr


def boundaries(
    feat: np.ndarray,
    frame_s: float,
    duration: float,
    drops: Sequence[float],
    edges: Sequence[float],
) -> List[Dict[str, float]]:
    """Interior boundaries ``{"t", "novelty"}``: peaks, plus drops and edges not near one."""
    nov = novelty_calc.novelty_curve(feat)
    found = [
        {"t": i * frame_s, "novelty": float(nov[i])} for i in novelty_calc.pick_peaks(nov, frame_s)
    ]

    def at(t: float) -> float:
        return float(nov[min(nov.size - 1, max(0, int(round(t / frame_s))))]) if nov.size else 0.0

    for t in [*drops, *edges]:
        if MIN_SEG_S < t < duration - MIN_SEG_S and all(abs(t - b["t"]) > MERGE_S for b in found):
            found.append({"t": float(t), "novelty": at(t)})
        else:
            near = [b for b in found if abs(t - b["t"]) <= MERGE_S]
            if near and t in drops:
                min(near, key=lambda b: abs(t - b["t"]))["t"] = float(t)  # the drop is exact
    return sorted(found, key=lambda b: b["t"])


def segments(
    y: np.ndarray,
    sr: int,
    duration: float,
    grid: np.ndarray,
    e_full: np.ndarray,
    e_struct: np.ndarray,
    drops: Sequence[float],
    content: Sequence[float],
) -> List[Dict[str, Any]]:
    feat, frame_s = features(y, sr)
    if feat.shape[1] < 3:
        return []
    edges = [t for t in content if MIN_SEG_S < t < duration - MIN_SEG_S]
    edges += quiet_region_edges(grid, e_struct, thresh=0.05, min_len=3.0)
    cuts = boundaries(feat, frame_s, duration, drops, edges)
    times = [0.0]
    strength = {0.0: 1.0}
    for b in cuts:
        if b["t"] - times[-1] >= MIN_SEG_S and duration - b["t"] >= MIN_SEG_S:
            times.append(round(b["t"], 3))
            strength[times[-1]] = b["novelty"]
    times.append(float(duration))
    raw, seg_feats = [], []
    for st, en in zip(times[:-1], times[1:]):
        mask = (grid >= st) & (grid < en)
        cols = feat[:, int(st / frame_s) : max(int(en / frame_s), int(st / frame_s) + 1)]
        seg_feats.append(cols.mean(axis=1))
        raw.append(
            {
                "start": round(st, 3),
                "end": round(en, 3),
                "energy_full": round(float(e_full[mask].mean()) if mask.any() else 0.0, 3),
                "energy_struct": round(float(e_struct[mask].mean()) if mask.any() else 0.0, 3),
                "boundary_novelty": round(strength.get(round(st, 3), 0.0), 4),
            }
        )
    groups = cluster_groups(seg_feats)
    for i, s in enumerate(raw):
        s["group"] = groups[i] if i < len(groups) else 0
    return raw
