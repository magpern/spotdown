"""The meter and downbeat groups of an analysis-v1 document (pure).

``grid.meter`` and ``grid.downbeats`` are ``heuristic`` and capped at 0.4 until held-out evidence
exists (``meter_calc.GATE_PASSED``, ``meter_calc.CAP``; spotdown#20). The schema holds one
confidence for the whole downbeat list; the per-bar confidences stay in the core's ``bars`` and
the list's confidence is their mean, so one unclear bar lowers it.
"""

from __future__ import annotations

from typing import Any, Dict


def beats_per_bar(core: Dict[str, Any]) -> int:
    """The estimated beats per bar, 4 when the meter was not claimed."""
    meter = core.get("meter")
    return int(meter["beats_per_bar"]) if meter else 4


def bar_seconds(core: Dict[str, Any]) -> float:
    return 60.0 * beats_per_bar(core) / core["bpm"]


def _clip(x: float) -> float:
    return round(min(1.0, max(0.0, float(x))), 3)


def meter_grid(core: Dict[str, Any]) -> Dict[str, Any]:
    """``{"meter", "downbeats"}`` or ``{}`` when the core made no meter claim."""
    meter = core.get("meter")
    bars = core.get("bars", [])
    if not meter or not bars or any("confidence" not in b for b in bars):
        return {}
    return {
        "meter": {
            "beats_per_bar": int(meter["beats_per_bar"]),
            "confidence": _clip(meter["confidence"]),
            "method": "heuristic",
        },
        "downbeats": {
            "times_s": [b["start"] for b in bars],
            "confidence": _clip(sum(b["confidence"] for b in bars) / len(bars)),
            "method": "heuristic",
        },
    }
