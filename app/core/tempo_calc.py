"""Tempo from an onset-strength envelope: a fine comb fit and an octave choice (pure, numpy only).

librosa's tracker quantises the tempo to its frame lags (about 2 %) and picks half or double time
at will on dense or sparse patterns. Here the tempo is fitted on a grid of beat positions, and
among the octave-equivalent candidates in 60-180 BPM the choice is a prior plus one measurement: the
half-time candidate lands on beats as loud as the ones it skips, so its contrast is near zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

LO_BPM, HI_BPM = 60.0, 180.0
SLOW_BPM = 72.0  # a slowest-octave candidate below this is taken as half time
FAST_BPM = 170.0
CONTRAST_MAX = 0.18
PULSE_MIN = 0.4  # a window with less than this share of the track's comb score has no pulse
TOL_FRAMES = 2


@dataclass
class TempoFit:
    bpm: float
    phase_s: float
    comb: float  # mean envelope at the grid beats (0-1 scale)
    contrast: float  # (on - off) / (on + off) against the half-way positions
    margin: float  # 0-1: how clear the half/double decision was (1 when there was none to make)
    candidates: List[Tuple[float, float, float]]  # (bpm, comb, contrast), every octave tried


def _at(env: np.ndarray, times: np.ndarray, hop_s: float) -> np.ndarray:
    """The envelope's local maximum within +-TOL_FRAMES of each time (jitter-tolerant)."""
    idx = np.round(times / hop_s).astype(int)
    out = np.zeros(idx.size)
    for d in range(-TOL_FRAMES, TOL_FRAMES + 1):
        j = np.clip(idx + d, 0, env.size - 1)
        out = np.maximum(out, env[j])
    return out


def _grid(duration_s: float, bpm: float, phase_s: float, shift: float = 0.0) -> np.ndarray:
    period = 60.0 / bpm
    return np.arange(phase_s + shift * period, duration_s, period)


def _comb(env: np.ndarray, hop_s: float, bpm: float, phase_s: float) -> float:
    g = _grid(env.size * hop_s, bpm, phase_s)
    return float(_at(env, g, hop_s).mean()) if g.size else 0.0


def best_phase(env: np.ndarray, hop_s: float, bpm: float, steps: int = 48) -> Tuple[float, float]:
    """``(phase_s, comb score)``: the grid offset that lines the beats up with the envelope."""
    period = 60.0 / bpm
    phases = np.linspace(0.0, period, steps, endpoint=False)
    scores = [_comb(env, hop_s, bpm, p) for p in phases]
    k = int(np.argmax(scores))
    return float(phases[k]), float(scores[k])


def refine(env: np.ndarray, hop_s: float, bpm: float, span: float = 0.035) -> Tuple[float, float]:
    """The ``(bpm, phase_s)`` in +-3.5 % of ``bpm`` with the best comb score, in two passes."""
    best = (bpm, 0.0, -1.0)
    for width, n in ((span, 29), (span / 14.0, 15)):
        centre = best[0] if best[2] >= 0 else bpm
        for b in np.linspace(centre * (1 - width), centre * (1 + width), n):
            phase, score = best_phase(env, hop_s, float(b), steps=32)
            if score > best[2] + 1e-9:
                best = (float(b), phase, score)
    return best[0], best[1]


def contrast(env: np.ndarray, hop_s: float, bpm: float, phase_s: float) -> float:
    dur = env.size * hop_s
    on = _at(env, _grid(dur, bpm, phase_s), hop_s)
    off = _at(env, _grid(dur, bpm, phase_s, 0.5), hop_s)
    if on.size == 0 or off.size == 0:
        return 0.0
    a, b = float(on.mean()), float(off.mean())
    return (a - b) / (a + b + 1e-9)


def seed_candidates(env: np.ndarray, hop_s: float, count: int = 6) -> List[float]:
    """Octave families from the autocorrelation peaks, as BPMs inside ``LO_BPM``-``HI_BPM``."""
    x = env - env.mean()
    n = x.size
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * n)) ** 2)[:n]
    lags = np.arange(1, n)
    bpm = 60.0 / (lags * hop_s)
    keep = (bpm >= LO_BPM / 2) & (bpm <= HI_BPM * 2)
    a = ac[1:]
    peaks = [i for i in np.flatnonzero(keep)[1:-1] if a[i] > a[i - 1] and a[i] >= a[i + 1]]
    peaks.sort(key=lambda i: -float(a[i]))
    out: List[float] = []
    for i in peaks[:count]:
        b = float(bpm[i])
        while b < LO_BPM:
            b *= 2
        while b > HI_BPM:
            b /= 2
        for c in (b, b * 2 if b * 2 <= HI_BPM else None, b / 2 if b / 2 >= LO_BPM else None):
            if c and not any(abs(c - o) / o < 0.04 for o in out):
                out.append(float(c))
    return out


def _merge(
    rows: List[Tuple[float, float, float, float]],
) -> List[Tuple[float, float, float, float]]:
    """Seeds of one tempo fit to the same value; keep the best-scoring one of each."""
    rows = sorted(rows, key=lambda r: r[0])
    merged: List[Tuple[float, float, float, float]] = []
    for r in rows:
        if merged and abs(r[0] - merged[-1][0]) / merged[-1][0] < 0.02:
            if r[2] > merged[-1][2]:
                merged[-1] = r
        else:
            merged.append(r)
    return merged


def choose_octave(slow: float, fast_ok: bool, contrast_slow: float) -> bool:
    """True to take the double of ``slow``. Below ``SLOW_BPM`` a candidate is rarely the beat; above
    it the double wins only when the in-between positions are as loud as the beats (a half-time
    candidate skips beats that carry onsets) and the double is not beyond ``FAST_BPM``."""
    if not fast_ok:
        return False
    return slow < SLOW_BPM or contrast_slow < CONTRAST_MAX


def fit_tempo(env: np.ndarray, hop_s: float) -> TempoFit | None:
    """The tempo, or ``None`` when the envelope has no periodic pulse at all."""
    if env.size < 8 or float(env.max()) <= 1e-9:
        return None
    seeds = seed_candidates(env, hop_s)
    if not seeds:
        return None
    rows = []
    for s in seeds:
        b, phase = refine(env, hop_s, s)
        rows.append((b, phase, _comb(env, hop_s, b, phase), contrast(env, hop_s, b, phase)))
    merged = _merge(rows)
    top = max(merged, key=lambda r: r[2])
    base = top[0]
    while base / 2 >= LO_BPM:  # the slowest member of the top candidate's octave family
        base /= 2
    slow = min(merged, key=lambda r: abs(r[0] - base) / base)
    fast_bpm = slow[0] * 2
    pick = slow
    margin = 1.0
    if fast_bpm <= FAST_BPM:
        by_prior = slow[0] < SLOW_BPM
        margin = (
            min(1.0, (SLOW_BPM - slow[0]) / 8.0)
            if by_prior
            else min(1.0, abs(slow[3] - CONTRAST_MAX) / 0.1)
        )
    if fast_bpm <= FAST_BPM and choose_octave(slow[0], True, slow[3]):
        b, phase = refine(env, hop_s, fast_bpm, span=0.02)
        pick = (b, phase, _comb(env, hop_s, b, phase), contrast(env, hop_s, b, phase))
    return TempoFit(
        bpm=pick[0],
        phase_s=pick[1],
        comb=pick[2],
        contrast=pick[3],
        margin=margin,
        candidates=[(round(r[0], 2), round(r[2], 3), round(r[3], 3)) for r in merged],
    )


def track_beats(
    env: np.ndarray,
    hop_s: float,
    bpm: float,
    phase_s: float,
    win_s: float = 8.0,
    step_s: float = 4.0,
) -> np.ndarray:
    """Beat times from a comb grid fitted in overlapping windows around the global tempo.

    Each window refines the tempo (+-2 %) and phase on its own slice, so a drifting tempo or a
    phase slip is followed; the middle ``step_s`` of each window supplies its beats. A window whose
    pulse is far weaker than the track's (an empty intro, a breakdown) supplies none.
    """
    dur = env.size * hop_s
    ref = max(_comb(env, hop_s, bpm, phase_s), 1e-9)
    beats: List[float] = []
    starts = np.arange(0.0, max(dur - win_s, 0.0) + step_s, step_s)
    for k, ws in enumerate(starts):
        we = min(ws + win_s, dur)
        seg = env[int(ws / hop_s) : int(we / hop_s)]
        if seg.size < 8:
            continue
        b, ph = refine(seg, hop_s, bpm, span=0.02)
        if _comb(seg, hop_s, b, ph) < PULSE_MIN * ref:
            continue
        lo = ws if k == 0 else ws + (win_s - step_s) / 2
        hi = we if k == len(starts) - 1 else ws + (win_s + step_s) / 2
        t = ws + ph + np.arange(0, int((we - ws) * b / 60.0) + 2) * 60.0 / b
        beats += [float(v) for v in t if lo <= v < hi]
    out = np.array(sorted(beats))
    if out.size:
        keep = np.concatenate([[True], np.diff(out) > 0.6 * 60.0 / bpm])
        out = out[keep]
    return out
