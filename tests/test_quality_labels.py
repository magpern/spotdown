"""Scoring section labels against annotated truth (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

from app.quality import labels_calc as lab

TRUTH: List[Dict[str, Any]] = [
    {"start": 0.0, "end": 10.0, "label": "intro"},
    {"start": 10.0, "end": 30.0, "label": "chorus"},
]


def _doc(rows: List[Any]) -> Dict[str, Any]:
    segments = [
        {"id": f"s{i + 1}", "start_s": a, "end_s": b} for i, (a, b, _l, _c) in enumerate(rows)
    ]
    labels = [
        {"segment_id": f"s{i + 1}", "label": lbl, "confidence": c, "method": "heuristic"}
        for i, (_a, _b, lbl, c) in enumerate(rows)
    ]
    return {"structure": {"segments": segments}, "labels": labels}


def test_a_perfect_labelling_scores_every_frame() -> None:
    doc = _doc([(0.0, 10.0, "intro", 0.7), (10.0, 30.0, "chorus", 0.8)])
    est = lab.estimated_spans(doc)
    assert lab.frame_counts(TRUTH, est, 0.0, 30.0) == (60, 60)
    assert lab.segment_outcomes(TRUTH, est, 0.0, 30.0) == [(0.7, True), (0.8, True)]


def test_a_wrong_label_costs_its_frames_and_marks_the_segment_wrong() -> None:
    est = lab.estimated_spans(_doc([(0.0, 10.0, "intro", 0.7), (10.0, 30.0, "verse", 0.6)]))
    assert lab.frame_counts(TRUTH, est, 0.0, 30.0) == (20, 60)
    assert lab.segment_outcomes(TRUTH, est, 0.0, 30.0) == [(0.7, True), (0.6, False)]


def test_missing_labels_never_score() -> None:
    assert lab.frame_counts(TRUTH, [], 0.0, 30.0) == (0, 60)
    assert lab.estimated_spans({"structure": {"segments": []}}) == []


def test_only_the_content_range_is_scored() -> None:
    est = lab.estimated_spans(_doc([(0.0, 10.0, "intro", 0.7), (10.0, 30.0, "chorus", 0.8)]))
    assert lab.frame_counts(TRUTH, est, 5.0, 20.0) == (30, 30)
    assert lab.segment_outcomes(TRUTH, est, 12.0, 30.0) == [(0.8, True)]


def test_a_segment_is_judged_by_the_truth_label_covering_most_of_it() -> None:
    est = lab.estimated_spans(_doc([(0.0, 14.0, "chorus", 0.5)]))  # 10 s intro, 4 s chorus
    assert lab.segment_outcomes(TRUTH, est, 0.0, 30.0) == [(0.5, False)]
