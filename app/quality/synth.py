"""Deterministic synthetic tracks with exact ground truth (pure numpy, no I/O).

A spec is plain JSON: a tempo, optional silence and fades, and sections of whole bars, each
naming the layers that play. ``render`` returns the mono signal and the truth that follows from
the spec by arithmetic, so nothing is annotated by hand and nothing depends on a detector.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

SR = 22050
STEPS = {  # sixteenth-note positions within a 4/4 bar
    "kick": (0, 4, 8, 12),
    "kick_break": (0, 7, 10),
    "kick_half": (0, 8),
    "snare": (4, 12),
    "hat": (2, 6, 10, 14),
    "hat16": tuple(range(0, 16, 2)),
}
PULSE = ("kick", "kick_break", "kick_half", "snare", "hat", "hat16")
GAIN = {"kick": 1.0, "snare": 0.6, "hat": 0.25, "bass": 0.55, "pad": 0.3, "lead": 0.3, "riser": 0.5}
BASS_NOTES = (55.0, 55.0, 65.4, 49.0)
NOISE_FLOOR = 1e-4


def _t(n: float) -> np.ndarray:
    return np.arange(int(n * SR)) / SR


def _kick() -> np.ndarray:
    t = _t(0.25)
    phase = 2 * np.pi * np.cumsum(45 + 90 * np.exp(-t / 0.03)) / SR
    return np.sin(phase) * np.exp(-t / 0.08)


def _snare(rng: np.random.Generator) -> np.ndarray:
    t = _t(0.15)
    return rng.standard_normal(t.size) * np.exp(-t / 0.05) * 0.7 + np.sin(2 * np.pi * 190 * t) * (
        np.exp(-t / 0.04) * 0.5
    )


def _hat(rng: np.random.Generator) -> np.ndarray:
    t = _t(0.06)
    return np.diff(rng.standard_normal(t.size + 1)) * np.exp(-t / 0.012)


def _add(y: np.ndarray, at_s: float, snippet: np.ndarray, gain: float) -> None:
    i = int(round(at_s * SR))
    if 0 <= i < y.size:
        seg = snippet[: y.size - i]
        y[i : i + seg.size] += gain * seg


def _tone(freqs: Tuple[float, ...], seconds: float, attack_s: float) -> np.ndarray:
    t = _t(seconds)
    env = np.minimum(1.0, t / attack_s) * np.minimum(1.0, (seconds - t) / attack_s)
    return sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs) * env  # type: ignore


def _section(
    y: np.ndarray, spec: Dict[str, Any], sec: Dict[str, Any], start: float, rng: np.random.Generator
) -> None:
    bar = 240.0 / spec["bpm"]
    step = bar / 16.0
    length = sec["bars"] * bar
    layers = sec["layers"]
    drums = {"kick": _kick(), "snare": _snare(rng), "hat": _hat(rng)}
    for name, gain in layers.items():
        if name in PULSE:
            base = name.split("_")[0].rstrip("16")
            for b in range(sec["bars"]):
                for s in STEPS[name]:
                    _add(y, start + b * bar + s * step, drums[base], gain * GAIN[base])
    for b in range(sec["bars"]):
        bs = start + b * bar
        if "bass" in layers:
            _add(
                y,
                bs,
                _tone((BASS_NOTES[b % 4], 2 * BASS_NOTES[b % 4]), bar, 0.02),
                layers["bass"] * GAIN["bass"],
            )
        if "lead" in layers:
            for k in range(4):
                f = 440.0 * (1.0, 1.25, 1.5, 1.875)[(k + b) % 4]
                _add(
                    y,
                    bs + k * bar / 4,
                    _tone((f, 2 * f), bar / 4, 0.005),
                    layers["lead"] * GAIN["lead"],
                )
    if "pad" in layers:
        _add(y, start, _tone((220.0, 277.2, 329.6), length, 0.4), layers["pad"] * GAIN["pad"])
    if "riser" in layers:
        t = _t(length)
        ramp = (t / length) ** 2
        sweep = np.sin(2 * np.pi * np.cumsum(200 + 1800 * t / length) / SR)
        _add(
            y,
            start,
            (rng.standard_normal(t.size) * 0.5 + sweep) * ramp,
            layers["riser"] * GAIN["riser"],
        )


def _four_on_the_floor(spec: Dict[str, Any]) -> bool:
    kicks = {k for s in spec["sections"] for k in s["layers"] if k.startswith("kick")}
    return kicks == {"kick"}


def _truth(spec: Dict[str, Any], t0: float, total: float) -> Dict[str, Any]:
    bar = 240.0 / spec["bpm"]
    beats: List[float] = []
    bounds: List[float] = []
    drops: List[float] = []
    builds: List[List[float]] = []
    prev = None
    at = t0
    for sec in spec["sections"]:
        end = at + sec["bars"] * bar
        if any(k in PULSE for k in sec["layers"]):
            beats += [at + i * bar / 4 for i in range(sec["bars"] * 4)]
        if prev is not None and set(prev) != set(sec["layers"]):
            bounds.append(at)
        if sec.get("drop"):
            drops.append(at)
        if "riser" in sec["layers"]:
            builds.append([at, end])
        prev = sec["layers"]
        at = end
    start, stop = spec["lead_in_s"], total - spec["tail_s"]
    edges = [start, stop]
    if spec["fade_in_s"]:
        edges.append(start + spec["fade_in_s"])
    if spec["fade_out_s"]:
        edges.append(stop - spec["fade_out_s"])
    if spec["lead_in_s"] >= 1.0:
        bounds.insert(0, start)
    if spec["tail_s"] >= 1.0:
        bounds.append(stop)

    def r(v: List[float]) -> List[float]:
        return [round(float(x), 3) for x in v]

    return {
        "duration_s": round(total, 3),
        "bpm": spec["bpm"],
        "four_on_the_floor": _four_on_the_floor(spec),
        "beats": r(beats),
        "boundaries": r(sorted(bounds)),
        "drops": r(drops),
        "build_ups": [r(b) for b in builds],
        "content_start_s": round(start, 3),
        "content_end_s": round(stop, 3),
        "fade_in_end_s": round(start + spec["fade_in_s"], 3) if spec["fade_in_s"] else None,
        "fade_out_start_s": round(stop - spec["fade_out_s"], 3) if spec["fade_out_s"] else None,
        "edges": r(sorted(edges)),
    }


def render(spec: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """The mono float32 signal at ``SR`` and its ground truth."""
    rng = np.random.default_rng(spec["seed"])
    bar = 240.0 / spec["bpm"]
    t0 = spec["lead_in_s"]
    music = sum(s["bars"] for s in spec["sections"]) * bar
    total = spec["lead_in_s"] + music + spec["tail_s"]
    y = np.zeros(int(round(total * SR)))
    at = t0
    for sec in spec["sections"]:
        _section(y, spec, sec, at, rng)
        at += sec["bars"] * bar
    a, b = int(spec["lead_in_s"] * SR), int(round((total - spec["tail_s"]) * SR))
    y[:a] = 0.0
    y[b:] = 0.0
    y /= max(float(np.abs(y).max()), 1e-9) / 0.8
    if spec["fade_in_s"]:
        n = int(spec["fade_in_s"] * SR)
        y[a : a + n] *= np.linspace(0.0, 1.0, n)
    if spec["fade_out_s"]:
        n = int(spec["fade_out_s"] * SR)
        y[b - n : b] *= np.linspace(1.0, 0.0, n)
    if spec.get("noise_db") is not None:
        y += rng.standard_normal(y.size) * 10 ** (spec["noise_db"] / 20.0) * (y != 0)
    y += rng.standard_normal(y.size) * NOISE_FLOOR
    return y.astype(np.float32), _truth(spec, t0, total)
