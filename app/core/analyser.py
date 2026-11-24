"""The core analyser: a mono signal in, features on the file's own clock out.

No source, no reference clock and no consumer: every time is seconds from the start of the decoded
signal. The backend (``app.service``) formats this result and feeds nothing back into
it.
"""

from __future__ import annotations

from typing import Any, Dict, List

import librosa
import numpy as np

from . import (
    ANALYZER_VERSION,
    HOP,
    SR,
    edges_calc,
    events_calc,
    meter_calc,
    signal_calc,
    tempo_calc,
)
from .segments import segments
from .signal_calc import norm95

__all__ = ["ANALYZER_VERSION", "SR", "analyse_signal"]

TEMPO_HOP = 256
TEMPO_FMAX = 1500
EVENT_STEP_S = 0.25


def meter_strengths(onset_env: np.ndarray, beat_frames: np.ndarray) -> np.ndarray:
    """The strongest onset within 2 frames of each tracked beat."""
    if not len(beat_frames) or not len(onset_env):
        return np.array([], dtype=float)
    idx = np.clip(beat_frames, 0, len(onset_env) - 1)
    return np.array([onset_env[max(0, i - 2) : i + 3].max() for i in idx], dtype=float)


def analyse_signal(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Analyse a mono float signal at ``sr`` Hz. Times are seconds on the signal's own clock."""
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Content boundaries (edge silence) and fades, from the frame-wise RMS.
    rms_full = librosa.feature.rms(y=y, hop_length=HOP)[0]
    hop_s = HOP / sr
    content_start, content_end = edges_calc.content_edges(rms_full, hop_s)
    fade_in = edges_calc.fade_in(rms_full, hop_s, content_start)
    fade_out = edges_calc.fade_out(rms_full, hop_s, content_end)

    # Tempo and beats: a comb fit on a low-band onset envelope, beats tracked in windows.
    t_hop = TEMPO_HOP
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=t_hop, fmax=TEMPO_FMAX)
    env = env / (float(np.percentile(env, 95)) or 1.0)
    fit = tempo_calc.fit_tempo(env, t_hop / sr)
    bpm = fit.bpm if fit else 0.0
    phase_s = fit.phase_s if fit else 0.0
    beat_times = (
        tempo_calc.track_beats(env, t_hop / sr, bpm, phase_s) if fit else np.array([], dtype=float)
    )
    beat_frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=HOP)

    # Energy: full-band, percussive, and low band (<150 Hz) — the last two expose
    # kick/bass changes that full-band RMS misses on loud masters.
    _, y_p = librosa.effects.hpss(y)
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

    _m = min(len(rms_perc), len(low_energy))

    # Drops and breakdowns: steps in the low-band / percussive energy at 0.25 s resolution,
    # snapped to the beat grid; build-ups: the climb before each drop.
    low_n, perc_n = norm95(low_energy[:_m]), norm95(rms_perc[:_m])
    step_s, low_b = signal_calc.bin_frames(low_n, hop_s, EVENT_STEP_S)
    perc_b = signal_calc.bin_frames(perc_n, hop_s, EVENT_STEP_S)[1]
    found = events_calc.detect(low_b, perc_b, step_s, bpm, phase_s)
    drops = [d["t"] for d in found["drops"]]
    build_ups = events_calc.build_ups(e_struct, 1.0, drops)

    # Meter and downbeats: the accent at each tracked beat (the onset strength around it), and
    # the content start and the drops as places where a bar starts.
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    strengths = meter_strengths(onset_env, beat_frames)
    meter = meter_calc.estimate(
        [float(t) for t in beat_times], strengths.tolist(), [content_start, *drops]
    )
    phase = 0
    if meter:
        bars = [{"index": i, **b} for i, b in enumerate(meter["bars"])]
        if bars and len(beat_times):
            phase = int(np.argmin(np.abs(beat_times - bars[0]["start"]))) % meter["beats_per_bar"]
    else:  # no claim: the legacy 4/4 grid on the strongest beat phase stays for the v1 endpoint
        phase = max(range(4), key=lambda p: float(strengths[p::4].sum())) if len(beat_times) else 0
        bars = [
            {"index": i, "start": round(float(t), 3)} for i, t in enumerate(beat_times[phase::4])
        ]

    bar_s = 60.0 * (meter["beats_per_bar"] if meter else 4) / bpm if bpm else 0.0
    segs = segments(
        y, sr, duration, grid, e_full, e_struct, drops, [content_start, content_end], bar_s
    )

    def _r(x: np.ndarray) -> List[float]:
        return [round(float(v), 3) for v in x]

    return {
        "duration_s": duration,
        "content_start_s": content_start,
        "content_end_s": content_end,
        "fade_in": fade_in,
        "fade_out": fade_out,
        "bpm": bpm,
        "tempo": {
            "phase_s": phase_s,
            "comb": fit.comb if fit else 0.0,
            "contrast": fit.contrast if fit else 0.0,
            "margin": fit.margin if fit else 0.0,
            "candidates": fit.candidates if fit else [],
        },
        "beat_times": [round(float(t), 3) for t in beat_times],
        "downbeat_phase": phase,
        "meter": {k: meter[k] for k in ("beats_per_bar", "confidence")} if meter else None,
        "bars": bars,
        "drops": drops,
        "drop_strengths": [d["strength"] for d in found["drops"]],
        "breakdowns": found["breakdowns"],
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
