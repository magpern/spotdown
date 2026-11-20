"""The gates file and its evaluation: hard gates are never relaxed, an unmeasured gate fails."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict

import pytest

from app.quality import gates_calc
from app.quality.runner import GATES


def _gates() -> Dict[str, Any]:
    return json.loads(GATES.read_text(encoding="utf-8"))


def _passing(gates: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {"false_exact": 0, "audio_kept_after_job": 0}
    for name, rule in gates["signal"].items():
        out[name] = rule["value"]
    return out


# Plan section 7 (Revision 2), all thirteen signal thresholds. Changing one needs ADR 0040.
PLAN_SECTION_7 = {
    "bpm_within_2pct_fraction": (">=", 0.95),
    "bpm_exact_octave_fraction": (">=", 0.85),
    "beat_f_four_on_the_floor": (">=", 0.85),
    "beat_f_overall": (">=", 0.75),
    "boundary_f_3s": (">=", 0.60),
    "edge_within_0_25s_fraction": (">=", 0.95),
    "drop_precision": (">=", 0.75),
    "drop_recall": (">=", 0.65),
    "ece_bpm": ("<=", 0.10),
    "ece_boundaries": ("<=", 0.10),
    "robustness_fraction": (">=", 0.99),
    "p95_seconds_per_5min": ("<=", 60),
    "peak_memory_gib": ("<=", 1.5),
}


# Enhancement gates (SD-P2+, spotdown#7): added with their own evidence, not in plan section 7.
ENHANCEMENT_GATES = {
    "label_accuracy": (">=", 0.70),
    "label_overconfidence": ("<=", 0.10),
    "phrase_f": (">=", 0.70),
    "phrase_overconfidence": ("<=", 0.03),
}


def test_the_committed_gates_are_valid_and_carry_every_plan_threshold() -> None:
    g = _gates()
    gates_calc.validate(g)
    assert g["version"] == 3
    assert g["hard"] == {"false_exact": 0, "audio_kept_after_job": 0}
    committed = {k: (v["op"], v["value"]) for k, v in g["signal"].items()}
    assert {k: v for k, v in committed.items() if k in PLAN_SECTION_7} == PLAN_SECTION_7
    assert {k: v for k, v in committed.items() if k not in PLAN_SECTION_7} == ENHANCEMENT_GATES
    assert g["parameters"]["bpm_tolerance"] == 0.02


def test_a_gate_missing_from_the_file_is_noticed() -> None:
    g = _gates()
    del g["signal"]["drop_recall"]
    assert set(PLAN_SECTION_7) - set(g["signal"]) == {"drop_recall"}


def test_values_exactly_at_the_thresholds_pass() -> None:
    g = _gates()
    assert gates_calc.failures(gates_calc.evaluate(g, _passing(g))) == []


def test_a_regression_fails_its_gate() -> None:
    g = _gates()
    metrics = _passing(g)
    metrics["boundary_f_3s"] = 0.59
    metrics["peak_memory_gib"] = 1.6
    assert gates_calc.failures(gates_calc.evaluate(g, metrics)) == [
        "boundary_f_3s",
        "peak_memory_gib",
    ]


def test_an_unmeasured_gate_fails() -> None:
    g = _gates()
    metrics = _passing(g)
    del metrics["drop_recall"]
    assert gates_calc.failures(gates_calc.evaluate(g, metrics)) == ["drop_recall"]


@pytest.mark.parametrize("name", ["false_exact", "audio_kept_after_job"])
def test_a_hard_gate_fails_on_any_nonzero_value(name: str) -> None:
    g = _gates()
    metrics = _passing(g)
    metrics[name] = 1
    assert gates_calc.failures(gates_calc.evaluate(g, metrics)) == [name]


@pytest.mark.parametrize("name", ["false_exact", "audio_kept_after_job"])
def test_a_file_that_relaxes_a_hard_gate_is_refused(name: str) -> None:
    g = copy.deepcopy(_gates())
    g["hard"][name] = 1
    with pytest.raises(gates_calc.GatesError):
        gates_calc.evaluate(g, _passing(g))
    del g["hard"][name]
    with pytest.raises(gates_calc.GatesError):
        gates_calc.validate(g)


def test_an_unknown_operator_is_refused() -> None:
    g = _gates()
    g["signal"]["ece_bpm"]["op"] = "!="
    with pytest.raises(gates_calc.GatesError):
        gates_calc.validate(g)
