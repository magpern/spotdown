"""Music-information-retrieval analysis for a downloaded track.

Produces beat/tempo, a percussive + low-band energy map, drops, build-ups and
beat-synchronous structural segments with heuristic labels. Everything is on the
*analyzed file's* timeline; the result reports how that compares to Spotify's
duration so a downstream consumer can tell whether timestamps line up.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import librosa
import numpy as np

# Bump when thresholds/features change so the cache (keyed on this) refreshes.
ANALYZER_VERSION = 2

# Spotify trims edge silence; if the music content matches within this many
# seconds we treat the timeline as alignable by a simple offset.
ALIGN_TOLERANCE_S = 1.5

SR = 22050
HOP = 512


def _tempo_candidates(bpm: float) -> List[float]:
    out = set()
    for mult in (0.5, 1.0, 2.0):
        v = bpm * mult
        if 40 <= v <= 240:
            out.add(round(v, 1))
    return sorted(out)


def _norm95(x: np.ndarray) -> np.ndarray:
    """Normalize by the 95th percentile (clipped to 1) so one transient can't flatten it."""
    if x.size == 0:
        return x
    p95 = float(np.percentile(x, 95))
    if p95 < 1e-9:
        p95 = 1.0
    return np.clip(x / p95, 0.0, 1.0)


def _snap(ts: float, arr: np.ndarray) -> float:
    if arr.size == 0:
        return round(float(ts), 3)
    j = int(np.argmin(np.abs(arr - ts)))
    return round(float(arr[j]), 3)


def _label(i: int, n: int, energy: float, hi: float, lo: float) -> str:
    if i == 0 and energy <= lo:
        return "intro"
    if i == n - 1 and energy <= lo:
        return "outro"
    if energy >= hi:
        return "chorus/drop"
    if energy <= lo:
        return "breakdown/quiet"
    return "verse"


def _cluster_groups(seg_feats: List[np.ndarray]) -> List[int]:
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


def _segments(y, sr, beat_frames, beat_times, duration, grid, e_full, e_struct) -> list:
    if len(beat_frames) < 6:
        return []
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)
    feat = np.vstack([
        librosa.util.normalize(chroma, axis=0),
        librosa.util.normalize(mfcc, axis=0),
    ])
    # Beat-synchronous features so boundaries land on beats.
    fsync = librosa.util.sync(feat, beat_frames, aggregate=np.mean)
    n = fsync.shape[1]
    if n < 3:
        return []
    k = int(max(3, min(10, round(duration / 20.0))))
    k = min(k, n - 1)
    try:
        bounds = librosa.segment.agglomerative(fsync, k)
    except Exception:
        return []
    bounds = sorted(set([0] + [int(b) for b in bounds] + [n]))

    # Column i of fsync spans edges[i]..edges[i+1] in time.
    edges = [0.0] + [float(t) for t in beat_times] + [float(duration)]

    def edge_time(idx: int) -> float:
        idx = max(0, min(idx, len(edges) - 1))
        return float(edges[idx])

    raw, seg_feats = [], []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        st, en = edge_time(a), edge_time(b)
        if en - st < 1.0:
            continue
        mask = (grid >= st) & (grid < en)
        ef = float(e_full[mask].mean()) if mask.any() else 0.0
        es = float(e_struct[mask].mean()) if mask.any() else 0.0
        seg_feats.append(fsync[:, a:b].mean(axis=1))
        raw.append({
            "start": round(st, 3), "end": round(en, 3),
            "energy_full": round(ef, 3), "energy_struct": round(es, 3),
        })
    if not raw:
        return []

    groups = _cluster_groups(seg_feats)
    structs = [s["energy_struct"] for s in raw]
    hi = float(np.percentile(structs, 60))
    lo = float(np.percentile(structs, 35))
    for i, s in enumerate(raw):
        s["group"] = groups[i] if i < len(groups) else 0
        s["label"] = _label(i, len(raw), s["energy_struct"], hi, lo)
    return raw


def analyze_track(file_path: Path, track_id: str,
                  spotify_duration_s: Optional[int] = None) -> dict:
    y, sr = librosa.load(str(file_path), sr=SR, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Content boundaries (edge silence trimmed). Spotify trims edge silence, so the
    # *content* duration — not the file duration — is what should match Spotify, and
    # content_start is the offset to subtract from a file time to reach Spotify's clock.
    _, idx = librosa.effects.trim(y, top_db=30)
    content_start = float(idx[0]) / sr
    content_end = float(idx[1]) / sr
    content_duration = max(content_end - content_start, 0.0)
    leading_silence = content_start
    trailing_silence = max(duration - content_end, 0.0)

    # Tempo + beats.
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP)
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)

    # Energy: full-band, percussive, and low band (<150 Hz) — the last two expose
    # kick/bass changes that full-band RMS misses on loud masters.
    _, y_p = librosa.effects.hpss(y)
    rms_full = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_perc = librosa.feature.rms(y=y_p, hop_length=HOP)[0]
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=64, fmax=4000)
    mel_f = librosa.mel_frequencies(n_mels=64, fmax=4000)
    low_mask = mel_f[:mel.shape[0]] < 150
    low_energy = mel[low_mask, :].mean(axis=0) if low_mask.any() else rms_perc
    t_frames = librosa.times_like(rms_full, sr=sr, hop_length=HOP)

    grid = np.arange(0.0, max(duration, 1.0), 1.0)
    e_full = _norm95(np.interp(grid, t_frames, rms_full))
    e_perc = _norm95(np.interp(grid, t_frames, rms_perc))
    e_low = _norm95(np.interp(grid, t_frames, low_energy))
    e_struct = _norm95(0.5 * e_perc + 0.5 * e_low)

    energy_curve = [
        {"t": float(t), "full": round(float(a), 3),
         "percussive": round(float(b), 3), "low": round(float(c), 3)}
        for t, a, b, c in zip(grid, e_full, e_perc, e_low)
    ]

    # Downbeat phase: the beat offset (0..3) with the most onset weight.
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    ob = onset_env[np.clip(beat_frames, 0, len(onset_env) - 1)]
    phase, best = 0, -1.0
    for p in range(4):
        s = float(ob[p::4].sum())
        if s > best:
            best, phase = s, p
    downbeat_times = beat_times[phase::4] if len(beat_times) > phase else beat_times
    bars = [{"index": i, "start": round(float(t), 3)} for i, t in enumerate(downbeat_times)]

    # Drops: structure-energy rising edges (low -> high), snapped to nearest beat.
    es = np.convolve(e_struct, np.ones(3) / 3.0, mode="same")
    drops: List[float] = []
    for i in range(2, len(es)):
        if es[i] > 0.6 and es[i - 2] < 0.35 and (not drops or grid[i] - drops[-1] > 8):
            drops.append(float(grid[i]))
    drops = [_snap(d, beat_times) for d in drops]

    # Build-ups: the sustained energy climb immediately before each drop.
    build_ups = []
    for d in drops:
        di = int(min(len(es) - 1, round(d)))
        s = di
        while s > 0 and es[s] >= es[s - 1] - 0.02 and (di - s) < 16:
            s -= 1
        if di - s >= 3:
            build_ups.append({"start": round(float(grid[max(s, 0)]), 3), "end": round(float(d), 3)})

    # Alignment: compare CONTENT duration (not file duration) to Spotify, and treat
    # content_start as the offset from the file clock to the Spotify clock.
    residual = (content_duration - spotify_duration_s) if spotify_duration_s else None
    matches = (abs(residual) <= ALIGN_TOLERANCE_S) if residual is not None else None
    # Spotify-clock cue points — only meaningful when the content aligns.
    drops_spotify = ([round(max(d - content_start, 0.0), 3) for d in drops]
                     if matches else None)

    return {
        "track_id": track_id,
        "analyzer_version": ANALYZER_VERSION,
        "sr": sr,
        "meter_assumed": "4/4",
        "label_method": "heuristic",
        "analyzed_duration_s": round(duration, 3),
        "spotify_duration_s": spotify_duration_s,
        "duration_delta_s": round(duration - spotify_duration_s, 3) if spotify_duration_s else None,
        "content_start_s": round(content_start, 3),
        "content_end_s": round(content_end, 3),
        "content_duration_s": round(content_duration, 3),
        "leading_silence_s": round(leading_silence, 3),
        "trailing_silence_s": round(trailing_silence, 3),
        # Subtract this from any file timestamp to get the Spotify-playback time.
        "spotify_offset_s": round(content_start, 3),
        "timeline_residual_s": round(residual, 3) if residual is not None else None,
        "timeline_matches_spotify": matches,
        "drops_spotify": drops_spotify,
        "bpm": round(bpm, 1),
        "bpm_candidates": _tempo_candidates(bpm),
        "downbeat_phase": phase,
        "beat_count": int(len(beat_times)),
        "beat_grid": [round(float(t), 3) for t in beat_times],
        "bars": bars,
        "drops": drops,
        "build_ups": build_ups,
        "energy_curve": energy_curve,
        "segments": _segments(y, sr, beat_frames, beat_times, duration, grid, e_full, e_struct),
    }
