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


def test_the_committed_gates_are_valid_and_carry_the_plan_thresholds() -> None:
    g = _gates()
    gates_calc.validate(g)
    assert g["version"] == 1
    assert g["hard"] == {"false_exact": 0, "audio_kept_after_job": 0}
    s = g["signal"]
    assert s["bpm_within_2pct_fraction"]["value"] == 0.95
    assert s["boundary_f_3s"]["value"] == 0.60
    assert s["peak_memory_gib"] == {"op": "<=", "value": 1.5}


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
