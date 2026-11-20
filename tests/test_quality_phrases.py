"""Phrase truth (arithmetic on the arrangement) and phrase scoring (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.quality import phrases_calc as ph
from app.quality.specs import SPECS
from app.quality.synth import _phrases


def _spec(*bars: int, bpm: float = 120.0, lead: float = 0.0) -> Dict[str, Any]:
    return {"bpm": bpm, "lead_in_s": lead, "sections": [{"bars": b} for b in bars]}


@pytest.mark.parametrize(
    ("bars", "expected"),
    [
        ((4, 4, 8, 4, 4, 8, 4), [8, 8, 8, 8]),
        ((8, 8, 16, 8), [8, 8, 16, 8]),
        ((3, 3, 3, 3), []),
        ((4, 8, 8, 8, 4), [8, 8, 8]),
        ((4, 4, 4), [8]),
    ],
)
def test_truth_groups_sections_into_8_and_16_bar_phrases(bars: Any, expected: List[int]) -> None:
    assert [p["bars"] for p in _phrases(_spec(*bars), 0.0)] == expected


def test_a_pickup_moves_the_first_phrase_and_the_lead_in_offsets_everything() -> None:
    found = _phrases(_spec(4, 8, 8, lead=1.0), 1.0)  # 2 s bars
    assert [(p["start"], p["end"]) for p in found] == [(9.0, 25.0), (25.0, 41.0)]


def test_the_committed_corpus_has_phrase_and_no_phrase_tracks_and_a_phrase_only_corpus() -> None:
    counts = {s["id"]: len(_phrases(s, s["lead_in_s"])) for s in SPECS}
    assert counts["slow-drop-74"] == 0 and counts["edm-100"] == 4
    assert {16} <= {p["bars"] for s in SPECS for p in _phrases(s, s["lead_in_s"])}
    assert {s["id"] for s in SPECS if s.get("corpus") == "phrases"} == {
        "pickup-edm-140",
        "pickup-groove-118",
    }


TRUTH = [{"start": 0.0, "end": 16.0, "bars": 8}, {"start": 16.0, "end": 32.0, "bars": 8}]


def _est(*rows: Any) -> List[Dict[str, Any]]:
    return [{"start": a, "end": b, "bars": 8, "confidence": c} for a, b, c in rows]


def test_matching_is_one_to_one_within_the_tolerance_on_both_ends() -> None:
    est = _est((0.2, 16.1, 0.4), (16.0, 33.5, 0.4), (40.0, 56.0, 0.3))
    assert ph.match(TRUTH, est, 1.0) == [(0.4, True), (0.4, False), (0.3, False)]


def test_two_estimates_cannot_claim_the_same_truth_phrase() -> None:
    est = _est((0.0, 16.0, 0.4), (0.1, 16.1, 0.4))
    assert sorted(ok for _, ok in ph.match(TRUTH, est, 1.0)) == [False, True]


def test_the_phrases_of_a_document_are_read_through_the_contract() -> None:
    doc = {"grid": {"phrases": [{"start_s": 1.0, "end_s": 9.0, "bars": 8, "confidence": 0.3}]}}
    assert ph.estimated(doc) == [{"start": 1.0, "end": 9.0, "bars": 8, "confidence": 0.3}]
    assert ph.estimated({"grid": {}}) == []


def _record(corpus: str, hits: List[Any], truth: int) -> Dict[str, Any]:
    return {
        "corpus": corpus,
        "phrase_hits": hits,
        "phrase_truth": truth,
        "boundary_f": 1.0,
        "beat_f": 1.0,
        "bpm_octave_ok": True,
        "bpm_exact_ok": True,
        "bpm_confidence": 0.9,
        "four_on_the_floor": False,
        "drops": [0, 0, 0],
        "edge_hits": [],
        "boundary_conf": [],
        "boundary_correct": [],
        "boundary_f_report": 1.0,
        "label_frames": [0, 0],
        "label_segments": [],
        "downbeat_tp": 0,
        "downbeat_est": 0,
        "downbeat_truth": 0,
        "downbeat_outcomes": [],
        "meter_true": 4,
        "meter_ok": True,
    }


def test_aggregate_pools_phrases_over_every_corpus_and_reports_each_corpus_apart() -> None:
    from app.quality.scoring_calc import aggregate

    base = _record("baseline", [(0.4, True), (0.4, True)], 2)
    pick = _record("phrases", [(0.4, False)], 3)  # one wrong phrase, three truth phrases missed
    out = aggregate([base, pick])
    # pooled (the gate): 2 right of 3 claimed, 2 of 5 true
    assert (out["phrase_precision"], out["phrase_recall"]) == (0.6667, 0.4)
    assert out["tracks"] == 2 and out["baseline_tracks"] == 1
    assert out["phrase_recall_baseline_reported"] == 1.0
    assert (out["phrase_f_pickup_reported"], out["phrase_overconfidence_pickup_reported"]) == (
        0.0,
        0.4,
    )
    assert out["phrase_recall_all_reported"] == out["phrase_recall"]
