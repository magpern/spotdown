"""Beat-synchronous structural segmentation (librosa), on the file's own clock."""

from __future__ import annotations

from typing import List

import librosa
import numpy as np

from . import HOP
from .signal_calc import quiet_region_edges, segment_label

MIN_SEG_S = 2.0


def cluster_groups(seg_feats: List[np.ndarray]) -> List[int]:
    """Group structurally-similar segments (so repeats share a group id)."""
    if len(seg_feats) < 2:
        return [0] * len(seg_feats)
    try:
        from scipy.cluster.hierarchy import fcluster, linkage

        X = np.vstack(seg_feats)
        Z = linkage(X, method="ward")
        ngroups = min(4, len(seg_feats))
        labels = fcluster(Z, t=ngroups, criterion="maxclust")
        return [int(v) for v in labels]
    except Exception:
        return [0] * len(seg_feats)


def segments(
    y, sr, beat_frames, beat_times, duration, grid, e_full, e_struct, struct_frames, drops
) -> list:
    if len(beat_frames) < 6:
        return []
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)
    feat = np.vstack(
        [
            librosa.util.normalize(chroma, axis=0),
            librosa.util.normalize(mfcc, axis=0),
        ]
    )
    fsync = librosa.util.sync(feat, beat_frames, aggregate=np.mean)
    n = fsync.shape[1]
    if n < 3:
        return []

    # Energy weighted into the beat-synchronous features so loud<->quiet shifts matter.
    esync = librosa.util.sync(struct_frames[np.newaxis, :], beat_frames, aggregate=np.mean)[0]
    esync = esync / (float(esync.max()) or 1.0)
    feat_full = np.vstack([fsync, np.tile(esync, (8, 1))])

    # Column i of fsync spans edges[i]..edges[i+1]; its center time locates it.
    edges = [0.0] + [float(t) for t in beat_times] + [float(duration)]
    col_center = np.array([0.5 * (edges[i] + edges[i + 1]) for i in range(n)])

    def edge_time(idx: int) -> float:
        return float(edges[max(0, min(idx, len(edges) - 1))])

    # Boundaries are assembled in TIME from three sources, so regions without a beat
    # grid (empty intros) are still resolved:
    k = int(max(4, min(16, round(duration / 12.0))))
    k = min(k, n - 1)
    try:
        beat_bounds = [edge_time(int(b)) for b in librosa.segment.agglomerative(feat_full, k)]
    except Exception:
        beat_bounds = []
    quiet_edges = quiet_region_edges(grid, e_struct, thresh=0.15, min_len=3.0)
    drop_times = [float(d) for d in drops]

    cand = {0.0, float(duration)}
    cand.update(beat_bounds)
    cand.update(quiet_edges)
    cand.update(drop_times)
    times = sorted(t for t in cand if 0.0 <= t <= duration)

    # Enforce a minimum segment length.
    merged = [times[0]]
    for t in times[1:]:
        if t - merged[-1] >= MIN_SEG_S:
            merged.append(t)
    if float(duration) - merged[-1] >= MIN_SEG_S:
        merged.append(float(duration))
    else:
        merged[-1] = float(duration)

    raw, seg_feats = [], []
    for st, en in zip(merged[:-1], merged[1:]):
        mask = (grid >= st) & (grid < en)
        ef = float(e_full[mask].mean()) if mask.any() else 0.0
        es = float(e_struct[mask].mean()) if mask.any() else 0.0
        cmask = (col_center >= st) & (col_center < en)
        segf = fsync[:, cmask].mean(axis=1) if cmask.any() else np.zeros(fsync.shape[0])
        seg_feats.append(segf)
        raw.append(
            {
                "start": round(st, 3),
                "end": round(en, 3),
                "energy_full": round(ef, 3),
                "energy_struct": round(es, 3),
            }
        )
    if not raw:
        return []

    groups = cluster_groups(seg_feats)
    structs = [s["energy_struct"] for s in raw]
    hi = float(np.percentile(structs, 60))
    lo = float(np.percentile(structs, 35))
    for i, s in enumerate(raw):
        s["group"] = groups[i] if i < len(groups) else 0
        s["label"] = segment_label(i, len(raw), s["energy_struct"], hi, lo)
    return raw
