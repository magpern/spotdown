"""The steady-energy check (spotdown#47): verse and chorus at almost the same loudness.

The two ``steady`` fixtures have long verses and short choruses whose drums and bass are the
same, told apart by a pad against a lead. The rule that called the loudest repeating group the
chorus called the verses chorus (more than half of the labelled content, no verse in one case).
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List

import pytest

from app.core import label_calc, novelty_calc
from app.core.analyser import analyse_signal
from app.quality import metrics_calc, specs, synth

STEADY = [s for s in specs.SPECS if s.get("corpus") == specs.STEADY]
BOUNDARY_TOL_S = 3.0  # quality/quality_gates.json parameters.boundary_tol_s
BOUNDARY_GATE = 0.60  # quality/quality_gates.json gates.boundary_f_3s


def _run(spec: Dict[str, Any]) -> Dict[str, Any]:
    y, truth = synth.render(spec)
    result = analyse_signal(y.astype("float64"), synth.SR)
    labels = label_calc.label_segments(
        result["segments"], result["drops"], result["content_start_s"], result["content_end_s"]
    )
    return {"result": result, "truth": truth, "labels": labels}


@functools.lru_cache(maxsize=None)
def _current(spec_id: str) -> Dict[str, Any]:
    """The run with the code as it is (cached: an analysis takes seconds); the tests that switch a
    rule off call ``_run`` directly."""
    return _run(next(s for s in STEADY if s["id"] == spec_id))


def _chorus_share(run: Dict[str, Any]) -> float:
    """The share of the labelled content time that is labelled chorus."""
    span: Dict[str, float] = {}
    for x in run["labels"]:
        seg = run["result"]["segments"][x["index"]]
        span[x["label"]] = span.get(x["label"], 0.0) + seg["end"] - seg["start"]
    return span.get("chorus", 0.0) / sum(span.values())


def _boundary_f(run: Dict[str, Any]) -> float:
    starts = [s["start"] for s in run["result"]["segments"][1:]]
    truth = run["truth"]
    return metrics_calc.boundary_f(truth["boundaries"], starts, truth["duration_s"], BOUNDARY_TOL_S)


def _verses(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [x for x in run["labels"] if x["label"] == "verse"]


def test_the_steady_specs_have_truth_at_every_section_change() -> None:
    assert [s["id"] for s in STEADY] == ["steady-120", "steady-140", "steady-subtle-120"]
    for spec in STEADY:
        truth = synth.render(spec)[1]
        labels = [x["label"] for x in truth["labels"]]
        assert labels[0] == "intro" and labels[-1] == "outro"
        assert {"verse", "chorus"} <= set(labels)
        assert len(truth["boundaries"]) == len(labels) - 1


@pytest.mark.parametrize("spec", STEADY, ids=lambda s: s["id"])
def test_at_most_half_the_content_is_chorus_and_a_verse_is_found(spec: Dict[str, Any]) -> None:
    run = _current(spec["id"])
    assert _chorus_share(run) <= 0.5
    assert len(_verses(run)) >= 1
    assert max(x["confidence"] for x in run["labels"]) <= label_calc.UNGATED_CAP  # spotdown#20


@pytest.mark.parametrize("spec", STEADY[:2], ids=lambda s: s["id"])
def test_the_check_bites_on_the_rule_before_the_margin(
    spec: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the margin off (the rule before spotdown#47) the loudest repeating group, the verses,
    is the chorus: the pair of assertions above fails."""
    monkeypatch.setattr(label_calc, "PEAK_MARGIN", -1.0)
    run = _run(spec)
    assert not (_chorus_share(run) <= 0.5 and len(_verses(run)) >= 1)


@pytest.mark.parametrize("spec", STEADY, ids=lambda s: s["id"])
def test_the_boundaries_meet_the_gate_and_the_segment_count_stays_within_the_bound(
    spec: Dict[str, Any],
) -> None:
    run = _current(spec["id"])
    assert _boundary_f(run) >= BOUNDARY_GATE
    assert len(run["result"]["segments"]) <= len(run["truth"]["labels"]) + 1  # spotdown#46's bound


def test_the_label_enum_is_unchanged() -> None:
    assert label_calc.LABELS == ("intro", "verse", "chorus", "bridge", "outro", "breakdown")
    for spec in STEADY:
        assert {x["label"] for x in _current(spec["id"])["labels"]} <= set(label_calc.LABELS)


def test_the_lower_peak_share_finds_the_faint_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The novelty change is what meets the gate on the subtle spec: with the contrast limit at 0
    (the peak share of before spotdown#47 on every track) it is cut into six parts and misses it."""
    spec = STEADY[2]
    assert _boundary_f(_current(spec["id"])) >= BOUNDARY_GATE
    monkeypatch.setattr(novelty_calc, "LOW_CONTRAST", 0.0)
    assert _boundary_f(_run(spec)) < BOUNDARY_GATE
