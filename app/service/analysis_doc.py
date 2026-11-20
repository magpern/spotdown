"""Formats a core-analyser result as an analysis-v1 document (schema/analysis-v1.json). Pure.

Times stay on the file's own clock. The confidences are first, documented estimates (beat
regularity, energy contrast); the quality harness (spotdown#6) calibrates them. Optional groups
(meter, downbeats, phrases, alignment) are left out until their gates pass: the meter is never
assumed to be 4/4. Section labels are always ``heuristic`` and, until their gate passes
(``label_calc.GATE_PASSED``, held-out evidence), capped at 0.4.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence

from ..core import ANALYZER_VERSION, HOP, SR, label_calc

ANALYZER_NAME = "spotdown"
# The core analyser's version, then this document mapping's version (1: section labels).
ANALYZER_VERSION_V2 = f"{ANALYZER_VERSION}.1"
MEASURED_CONFIDENCE = 0.9


def config_hash() -> str:
    config = {"analyzer": ANALYZER_VERSION_V2, "sr": SR, "hop": HOP}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def fingerprint(energy_full: Sequence[float]) -> str:
    """A coarse decoded-audio fingerprint: the 1 s energy curve quantised to 16 levels.

    An equivalence signal only (re-encodes of the same audio tend to share it); never a key.
    """
    levels = bytes(min(15, max(0, int(v * 16))) for v in energy_full)
    return "energy16-v1:" + hashlib.sha256(levels).hexdigest()[:32]


def _clip(x: float) -> float:
    return round(min(1.0, max(0.0, float(x))), 3)


def beat_stability(beats: Sequence[float]) -> float:
    """The share of inter-beat gaps within 10 % of the median gap: 1 for an even grid, and a gap
    where beats are absent (a breakdown) costs only that gap rather than the whole score."""
    if len(beats) < 4:
        return 0.0
    gaps = sorted(b - a for a, b in zip(beats[:-1], beats[1:]))
    median = gaps[len(gaps) // 2]
    if median <= 0:
        return 0.0
    return _clip(sum(1 for g in gaps if abs(g - median) <= 0.1 * median) / len(gaps))


def _curve(values: Sequence[float]) -> Dict[str, Any]:
    return {
        "step_s": 1.0,
        "values": [_clip(v) for v in values],
        "confidence": MEASURED_CONFIDENCE,
        "method": "measured",
    }


def _mean(values: Sequence[float], start: float, end: float) -> float:
    window = [v for t, v in enumerate(values) if start <= t < end]
    return sum(window) / len(window) if window else 0.0


def _fade(fade: Optional[Sequence[float]]) -> Optional[Dict[str, Any]]:
    if not fade:
        return None
    return {
        "start_s": round(fade[0], 3),
        "end_s": round(fade[1], 3),
        "confidence": _clip(fade[2]),
        "method": "estimated",
    }


def tempo_confidence(core: Dict[str, Any], stability: float) -> float:
    """How much to trust the BPM, from four measured things: the pulse strength at the fitted grid,
    how clear the half/double-time decision was, how steady the tracked beats are, and how many
    there are. Capped below 1: a tempo is an estimate. Calibrated on the synthetic corpus only
    (spotdown#6); the owner corpus recalibrates it."""
    t = core["tempo"]
    pulse = min(1.0, t["comb"] / 0.8)
    clarity = 0.8 + 0.2 * t.get("margin", 1.0)
    steady = 0.85 + 0.15 * stability
    coverage = min(1.0, len(core["beat_times"]) / 16.0)
    return _clip(0.97 * pulse * clarity * steady * coverage)


def _silences(duration: float, start: float, end: float) -> List[Dict[str, Any]]:
    regions = []
    if start > 0.05:
        regions.append((0.0, start))
    if duration - end > 0.05:
        regions.append((end, duration))
    return [
        {"start_s": round(a, 3), "end_s": round(b, 3), "confidence": 0.8, "method": "measured"}
        for a, b in regions
    ]


NOVELTY_SCALE = 0.08  # the novelty at which a boundary is trusted about 63 % (calibration)


def boundary_confidence(novelty: Optional[float], first: bool) -> float:
    """From the self-similarity novelty at the boundary: a saturating curve, capped below 1.
    The first segment starts the file, so nothing is claimed about it beyond a measured start."""
    if first or novelty is None:
        return 0.9 if first else 0.3
    return _clip(0.95 * (1.0 - math.exp(-max(0.0, novelty) / NOVELTY_SCALE)))


def _structure(core: Dict[str, Any]) -> Dict[str, Any]:
    segments, groups = [], {}
    for i, seg in enumerate(core["segments"]):
        sid = f"s{i + 1}"
        segments.append(
            {
                "id": sid,
                "start_s": seg["start"],
                "end_s": seg["end"],
                "boundary_confidence": boundary_confidence(seg.get("boundary_novelty"), i == 0),
                "method": "estimated",
            }
        )
        groups.setdefault(seg.get("group", 0), []).append(sid)
    repetition = [
        {"segment_ids": ids, "confidence": 0.5, "method": "estimated"}
        for _, ids in sorted(groups.items())
        if len(ids) >= 2
    ]
    return {"segments": segments, "repetition_groups": repetition}


def _labels(core: Dict[str, Any]) -> List[Dict[str, Any]]:
    found = label_calc.label_segments(
        core["segments"], core["drops"], core["content_start_s"], core["content_end_s"]
    )
    return [
        {
            "segment_id": f"s{x['index'] + 1}",
            "label": x["label"],
            "confidence": _clip(x["confidence"]),
            "method": "heuristic",
        }
        for x in found
    ]


def _events(core: Dict[str, Any]) -> List[Dict[str, Any]]:
    struct = core["energy"]["struct"]
    events: List[Dict[str, Any]] = []
    for b in core["build_ups"]:
        rise = _mean(struct, b["end"] - 2, b["end"]) - _mean(struct, b["start"], b["start"] + 2)
        events.append(
            {
                "kind": "build_up",
                "start_s": b["start"],
                "end_s": b["end"],
                "strength": _clip(rise),
                "confidence": 0.5,
                "method": "estimated",
            }
        )
    strengths = core.get("drop_strengths") or [0.5] * len(core["drops"])
    for d, jump in zip(core["drops"], strengths):
        events.append(
            {
                "kind": "drop",
                "start_s": d,
                "strength": _clip(jump),
                "confidence": _clip(0.4 + 0.5 * jump),
                "method": "estimated",
            }
        )
    for bd in core.get("breakdowns", []):
        events.append(
            {
                "kind": "breakdown",
                "start_s": bd["t"],
                "strength": _clip(bd["strength"]),
                "confidence": _clip(0.3 + 0.6 * bd["strength"]),
                "method": "estimated",
            }
        )
    return sorted(events, key=lambda e: e["start_s"])


def build(
    *,
    core: Dict[str, Any],
    probe: Dict[str, Any],
    components: Dict[str, str],
    sha256: str,
    source: Dict[str, Any],
    job_id: str,
    analysis_id: str,
    timings_s: Dict[str, float],
    created_at: str,
) -> Dict[str, Any]:
    duration = round(core["duration_s"], 3)
    start = round(core["content_start_s"], 3)
    end = round(core["content_end_s"], 3)
    if not core["bpm"] > 0:
        raise ValueError("no tempo found")
    beats = core["beat_times"]
    stability = beat_stability(beats)
    bpm = round(core["bpm"], 2)
    tempo_conf = tempo_confidence(core, stability)
    audio: Dict[str, Any] = {
        "sha256": sha256,
        "fingerprint": fingerprint(core["energy"]["full"]),
        "codec": probe["codec"],
        "sample_rate_hz": probe["sample_rate_hz"],
        "channels": probe["channels"],
        "file_duration_s": duration,
        "content_start_s": start,
        "content_end_s": end,
        "content_duration_s": round(max(end - start, 0.0), 3),
    }
    if probe.get("bitrate_bps"):
        audio["bitrate_bps"] = int(probe["bitrate_bps"])
    tempo: Dict[str, Any] = {
        "bpm": bpm,
        "confidence": tempo_conf,
        "method": "estimated",
        "half_double_candidates": [
            {"bpm": round(bpm * f, 2), "confidence": _clip(tempo_conf * 0.3), "method": "estimated"}
            for f in (0.5, 2.0)
        ],
        "stability": stability,
        "change_points": [],
    }
    doc: Dict[str, Any] = {
        "schema_version": "analysis-v1",
        "analysis_id": analysis_id,
        "provenance": {
            "analyzer": {
                "name": ANALYZER_NAME,
                "analyzer_version": ANALYZER_VERSION_V2,
                "component_versions": dict(sorted(components.items())),
                "config_hash": config_hash(),
            },
            "audio": audio,
            "source": source,
            "run": {
                "job_id": job_id,
                "status": "done",
                "failure_kind": None,
                "timings_s": {k: round(max(0.0, v), 3) for k, v in timings_s.items()},
            },
            "created_at": created_at,
        },
        "signal": {
            "duration_s": duration,
            "content_start_s": start,
            "content_end_s": end,
            "method": "measured",
            "confidence": MEASURED_CONFIDENCE,
            "silence_regions": _silences(duration, start, end),
            "fade_in": _fade(core.get("fade_in")),
            "fade_out": _fade(core.get("fade_out")),
            "energy": {
                "full": _curve(core["energy"]["full"]),
                "percussive": _curve(core["energy"]["percussive"]),
                "low_band": _curve(core["energy"]["low"]),
            },
        },
        "tempo": tempo,
        "grid": {"beats": {"times_s": beats, "confidence": stability, "method": "estimated"}},
        "structure": _structure(core),
        "events": _events(core),
    }
    labels = _labels(core)
    if labels:
        doc["labels"] = labels
    return doc
