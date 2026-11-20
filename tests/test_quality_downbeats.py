"""Downbeat and meter truth (arithmetic on the arrangement) and scoring (pure)."""

from __future__ import annotations

from typing import Any, Dict, List

from app.quality import downbeats_calc as db
from app.quality.scoring_calc import aggregate
from app.quality.specs import METER, SPECS
from app.quality.synth import _truth


def _spec(bpb: int, *bars: int, bpm: float = 120.0) -> Dict[str, Any]:
    layers = {"kick": 1.0}
    return {
        "bpm": bpm,
        "beats_per_bar": bpb,
        "lead_in_s": 0.0,
        "tail_s": 0.0,
        "fade_in_s": 0.0,
        "fade_out_s": 0.0,
        "sections": [{"bars": b, "label": "x", "layers": layers} for b in bars],
    }


def test_truth_downbeats_are_the_bar_starts_of_the_meter_of_the_spec() -> None:
    three = _truth(_spec(3, 2, 2), 0.0, 6.0)  # 120 bpm: a 3/4 bar is 1.5 s
    assert three["beats_per_bar"] == 3 and three["downbeats"] == [0.0, 1.5, 3.0, 4.5]
    assert len(three["beats"]) == 12 and three["four_on_the_floor"] is False
    four = _truth(_spec(4, 2), 0.0, 4.0)
    assert four["beats_per_bar"] == 4 and four["downbeats"] == [0.0, 2.0]


def test_the_meter_corpus_is_all_three_four_and_the_rest_four_four() -> None:
    meter = {s["id"] for s in SPECS if s.get("corpus") == METER}
    assert len(meter) == 4 and all(s["beats_per_bar"] == 3 for s in SPECS if s["id"] in meter)
    assert all(s.get("beats_per_bar", 4) == 4 for s in SPECS if s["id"] not in meter)


def _doc(times: List[float], meter: Any = 4, conf: float = 0.3) -> Dict[str, Any]:
    grid: Dict[str, Any] = {}
    if meter:
        grid["meter"] = {"beats_per_bar": meter, "confidence": 0.3, "method": "heuristic"}
        grid["downbeats"] = {"times_s": times, "confidence": conf, "method": "heuristic"}
    return {"grid": grid}


TRUTH = {"downbeats": [6.0, 8.0, 10.0, 12.0], "beats_per_bar": 4}


def test_downbeats_match_one_to_one_within_the_tolerance_and_the_early_ones_are_trimmed() -> None:
    got = db.score(TRUTH, _doc([1.0, 6.03, 8.2, 10.0, 10.02]), 0.07)
    assert (got["downbeat_tp"], got["downbeat_est"], got["downbeat_truth"]) == (2, 4, 4)
    assert got["meter_ok"] is True
    assert got["downbeat_outcomes"] == [(0.3, True), (0.3, False), (0.3, True), (0.3, True)]


def test_no_claim_scores_no_downbeats_and_a_wrong_meter() -> None:
    got = db.score(TRUTH, _doc([], meter=None), 0.07)
    assert (got["downbeat_est"], got["downbeat_truth"], got["meter_ok"]) == (0, 4, False)
    assert db.score(TRUTH, _doc([6.0], meter=3), 0.07)["meter_ok"] is False


def _record(bpb: int, tp: int, est: int, truth: int, ok: bool, conf: float = 0.3) -> Dict[str, Any]:
    return {
        "downbeat_tp": tp,
        "downbeat_est": est,
        "downbeat_truth": truth,
        "downbeat_outcomes": [(conf, i < tp) for i in range(est)],
        "meter_true": bpb,
        "meter_ok": ok,
    }


def test_pooling_is_per_true_meter_so_a_weak_class_is_not_hidden() -> None:
    four = [_record(4, 9, 10, 10, True), _record(4, 10, 10, 10, True)]
    three = [_record(3, 0, 10, 10, False)]
    assert db.pooled(four)["f"] > 0.9 and db.pooled(three)["f"] == 0.0
    assert db.meter_accuracy(four + three, 4) == 1.0
    assert db.meter_accuracy(four + three, 3) == 0.0
    assert db.meter_accuracy(four, 3) == 1.0  # no track of that meter: nothing to miss


def test_overconfidence_counts_only_a_claim_above_the_hit_rate() -> None:
    sure = [_record(4, 5, 10, 10, True, conf=0.9)]  # claims 0.9, right half the time
    modest = [_record(4, 5, 10, 10, True, conf=0.3)]
    assert db.pooled(sure)["overconfidence"] > 0.3 and db.pooled(modest)["overconfidence"] == 0.0


def test_aggregate_reports_each_meter_apart_and_the_minimum_class_accuracy() -> None:
    base: Dict[str, Any] = {
        "corpus": "baseline", "phrase_hits": [], "phrase_truth": 0, "boundary_f": 1.0,
        "beat_f": 1.0, "bpm_octave_ok": True, "bpm_exact_ok": True, "bpm_confidence": 0.9,
        "four_on_the_floor": False, "drops": [0, 0, 0], "edge_hits": [], "boundary_conf": [],
        "boundary_correct": [], "boundary_f_report": 1.0, "label_frames": [0, 0],
        "label_segments": [],
    }  # fmt: skip
    out = aggregate(
        [
            dict(base, **_record(4, 9, 10, 10, True)),
            dict(base, corpus=METER, **_record(3, 5, 10, 10, False)),
        ]
    )
    assert out["downbeat_f_4_4"] > 0.85 and out["downbeat_f_3_4"] == 0.5
    assert out["meter_accuracy_4_4"] == 1.0 and out["meter_accuracy_3_4"] == 0.0
    assert out["meter_accuracy_min"] == 0.0
