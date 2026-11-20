"""``grid.meter`` and ``grid.downbeats`` of the document: heuristic, capped, schema valid."""

from __future__ import annotations

from typing import Any, Dict

from app.service.meter_doc import bar_seconds, meter_grid
from tests.service_helpers import assert_valid, canned_core
from tests.test_analysis_doc import build


def _core(meter: Any = None) -> Dict[str, Any]:
    core = canned_core(40)
    core["meter"] = meter
    core["bars"] = [
        {"index": i, "start": 2.0 * i, "confidence": c} for i, c in enumerate((0.3, 0.2, 0.4))
    ]
    return core


def test_a_claimed_meter_gives_heuristic_meter_and_downbeats_with_the_mean_confidence() -> None:
    doc = build(_core({"beats_per_bar": 3, "confidence": 0.31}), {"kind": "upload"})
    assert_valid("analysis-v1", doc)
    grid = doc["grid"]
    assert grid["meter"] == {"beats_per_bar": 3, "confidence": 0.31, "method": "heuristic"}
    assert grid["downbeats"] == {
        "times_s": [0.0, 2.0, 4.0],
        "confidence": 0.3,
        "method": "heuristic",
    }


def test_no_meter_claim_means_no_meter_and_no_downbeats_and_four_four_phrases() -> None:
    core = _core(None)
    assert meter_grid(core) == {}
    assert "meter" not in build(core, {"kind": "upload"})["grid"]
    assert bar_seconds(core) == 240.0 / core["bpm"]


def test_phrases_count_bars_of_the_estimated_meter() -> None:
    three = _core({"beats_per_bar": 3, "confidence": 0.3})
    assert bar_seconds(three) == 180.0 / three["bpm"]
