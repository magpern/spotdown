"""The core analyser: a mono signal in, features on the file's own clock out.

No source, no reference clock and no consumer: every time is seconds from the start of the decoded
signal. The standalone v1 endpoint (``app.analyze``) and the service mode (``app.service``) both
format this result; neither feeds anything back into it.
"""

from __future__ import annotations

from typing import Any, Dict, List

import librosa
import numpy as np

from . import ANALYZER_VERSION, HOP, SR
from .segments import segments
from .signal_calc import norm95, rising_edges, snap

__all__ = ["ANALYZER_VERSION", "SR", "analyse_signal"]


def analyse_signal(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Analyse a mono float signal at ``sr`` Hz. Times are seconds on the signal's own clock."""
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Content boundaries (edge silence trimmed).
    _, idx = librosa.effects.trim(y, top_db=30)
    content_start = float(idx[0]) / sr
    content_end = float(idx[1]) / sr

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
    low_mask = mel_f[: mel.shape[0]] < 150
    low_energy = mel[low_mask, :].mean(axis=0) if low_mask.any() else rms_perc
    t_frames = librosa.times_like(rms_full, sr=sr, hop_length=HOP)

    grid = np.arange(0.0, max(duration, 1.0), 1.0)
    e_full = norm95(np.interp(grid, t_frames, rms_full))
    e_perc = norm95(np.interp(grid, t_frames, rms_perc))
    e_low = norm95(np.interp(grid, t_frames, low_energy))
    e_struct = norm95(0.5 * e_perc + 0.5 * e_low)

    # Frame-level structural energy (for beat-synchronous segmentation).
    _m = min(len(rms_perc), len(low_energy))
    struct_frames = norm95(0.5 * norm95(rms_perc[:_m]) + 0.5 * norm95(low_energy[:_m]))

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

    # Drops: rising edges detected on the LOW band (bass drops) and the PERCUSSIVE
    # band (beat drops) separately, then merged — averaging the two hides bass-only drops.
    raw_drops = sorted(
        rising_edges(e_low, grid, hi=0.5, lo=0.25) + rising_edges(e_perc, grid, hi=0.6, lo=0.35)
    )
    drops: List[float] = []
    for d in raw_drops:
        if not drops or d - drops[-1] > 4:
            drops.append(d)
    drops = [snap(d, beat_times) for d in drops]

    # Build-ups: the sustained energy climb immediately before each drop.
    es = np.convolve(e_struct, np.ones(3) / 3.0, mode="same")
    build_ups = []
    for d in drops:
        di = int(min(len(es) - 1, round(d)))
        s = di
        while s > 0 and es[s] >= es[s - 1] - 0.02 and (di - s) < 16:
            s -= 1
        if di - s >= 3:
            build_ups.append({"start": round(float(grid[max(s, 0)]), 3), "end": round(float(d), 3)})

    segs = segments(
        y, sr, beat_frames, beat_times, duration, grid, e_full, e_struct, struct_frames, drops
    )

    def _r(x: np.ndarray) -> List[float]:
        return [round(float(v), 3) for v in x]

    return {
        "duration_s": duration,
        "content_start_s": content_start,
        "content_end_s": content_end,
        "bpm": bpm,
        "beat_times": [round(float(t), 3) for t in beat_times],
        "downbeat_phase": phase,
        "bars": bars,
        "drops": drops,
        "build_ups": build_ups,
        "energy": {
            "t": [float(t) for t in grid],
            "full": _r(e_full),
            "percussive": _r(e_perc),
            "low": _r(e_low),
            "struct": _r(e_struct),
        },
        "segments": segs,
    }
