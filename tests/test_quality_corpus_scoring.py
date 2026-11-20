"""Scoring a track of an external corpus: partial annotation, per-metric counts, and the gates that
must be able to fail for an inflated or a wrong result."""

from __future__ import annotations

from typing import Any, Dict, List

from app.quality import corpus_scoring_calc as sc
from tests.corpus_helpers import TOL, make_doc, truth_for


def rec(truth: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    return sc.score_track(truth, sc.estimate_from_doc(doc, TOL), TOL)


def beats(bpm: float = 120.0, dur: float = 100.0) -> List[float]:
    return [round(i * 60.0 / bpm, 4) for i in range(int(dur * bpm / 60.0))]


def test_a_perfect_track_scores_one_everywhere() -> None:
    b = beats()
    r = rec(truth_for(b, drops=[30.0]), make_doc(beats=b, drops=[30.0]))
    assert r["bpm_octave_ok"] and r["bpm_exact_ok"] and r["beat_f"] == 1.0
    assert r["boundary_f"] == 1.0 and r["edge_hits"] == [True, True] and r["drops"] == [1, 0, 0]


def test_a_metric_is_none_where_the_track_is_not_annotated() -> None:
    r = rec({"bpm": 120.0}, make_doc())
    assert "bpm_octave_ok" in r
    assert not {"beat_f", "boundary_f", "edge_hits", "drops"} & set(r)
    agg = sc.aggregate([r])
    assert agg["beat_f_overall"] is None and agg["n_beat_f_overall"] == 0.0
    assert agg["bpm_within_2pct_fraction"] == 1.0 and agg["n_bpm_within_2pct_fraction"] == 1.0


def test_an_empty_annotation_is_an_annotation() -> None:
    quiet = rec(truth_for(beats(), drops=[]), make_doc(drops=[]))
    noisy = rec(truth_for(beats(), drops=[]), make_doc(drops=[40.0]))
    assert quiet["drops"] == [0, 0, 0] and noisy["drops"] == [0, 1, 0]
    assert sc.aggregate([noisy])["drop_precision"] == 0.0


def test_no_drop_claimed_is_no_precision_not_a_perfect_one() -> None:
    silent = rec(truth_for(beats(), drops=[40.0]), make_doc(drops=[]))
    agg = sc.aggregate([silent])
    assert agg["drop_precision"] is None and agg["n_drop_precision"] == 0.0
    assert agg["drop_recall"] == 0.0  # the gate that can still fail
    claimed = sc.aggregate([rec(truth_for(beats(), drops=[]), make_doc(drops=[40.0]))])
    assert claimed["n_drop_precision"] == 1.0


def test_octave_errors_pass_the_octave_gate_but_not_the_exact_one() -> None:
    r = rec(truth_for(beats(), bpm=120.0), make_doc(bpm=60.0))
    assert r["bpm_octave_ok"] and not r["bpm_exact_ok"]
    assert sc.aggregate([r])["bpm_exact_octave_fraction"] == 0.0


def test_a_confident_wrong_tempo_gives_a_large_calibration_error() -> None:
    wrong = [rec(truth_for(beats(), bpm=100.0), make_doc(bpm=120.0, conf=0.99)) for _ in range(5)]
    # an inflated confidence on a wrong answer is what the calibration gate must catch
    assert (sc.aggregate(wrong)["ece_bpm"] or 0.0) >= 0.9


def test_calibration_of_boundaries_uses_their_confidence() -> None:
    t = truth_for(beats(), boundaries=[30.0])
    hit = rec(t, make_doc(bounds=[30.0], bound_conf=0.9))
    miss = rec(t, make_doc(bounds=[80.0], bound_conf=0.9))
    assert (sc.aggregate([hit])["ece_boundaries"] or 9.0) < 0.11
    assert (sc.aggregate([miss])["ece_boundaries"] or 0.0) > 0.89


def test_four_on_the_floor_is_pooled_apart_from_the_rest() -> None:
    b = beats()
    good = rec(truth_for(b, four_on_the_floor=True), make_doc(beats=b))
    bad = rec(truth_for(b), make_doc(beats=[t + 0.3 for t in b]))
    agg = sc.aggregate([good, bad])
    assert agg["beat_f_four_on_the_floor"] == 1.0 and agg["n_beat_f_four_on_the_floor"] == 1.0
    assert agg["beat_f_overall"] == 0.5


def test_one_weak_track_is_visible_and_not_hidden_by_a_count() -> None:
    b = beats()
    tracks = [rec(truth_for(b), make_doc(beats=b)) for _ in range(9)]
    tracks.append(rec(truth_for(b), make_doc(beats=[t + 0.3 for t in b])))
    agg = sc.aggregate(tracks)
    assert agg["beat_f_overall"] == 0.9 and agg["n_beat_f_overall"] == 10.0


def test_bpm_at_follows_the_tempo_changes() -> None:
    tl = [(0.0, 120.0), (60.0, 90.0)]
    assert [sc.bpm_at(tl, t) for t in (0.0, 59.9, 60.0, 99.0)] == [120.0, 120.0, 90.0, 90.0]


def test_a_mix_is_scored_per_song_edge_and_per_song_tempo() -> None:
    songs = [
        {"start_s": 0.0, "end_s": 100.0, "bpm": 120.0},
        {"start_s": 100.0, "end_s": 200.0, "bpm": 90.0},
    ]
    doc = make_doc(
        duration=200.0,
        content=(0.0, 200.0),
        bounds=[101.5],
        changes=[{"time_s": 101.5, "bpm": 90.0, "confidence": 0.8}],
    )
    r = rec({"songs": songs}, doc)
    assert r["song_edge_hits"] == [True, True, True, True]
    assert r["song_bpm_hits"] == [True, True]
    agg = sc.aggregate([r])
    assert agg["song_start_end_within_2s_fraction"] == 1.0
    assert (
        agg["song_bpm_within_2pct_fraction"] == 1.0
        and agg["n_song_bpm_within_2pct_fraction"] == 2.0
    )


def test_a_mix_missing_a_song_boundary_by_more_than_2s_misses() -> None:
    songs = [
        {"start_s": 0.0, "end_s": 100.0, "bpm": 120.0},
        {"start_s": 100.0, "end_s": 200.0, "bpm": 120.0},
    ]
    doc = make_doc(duration=200.0, content=(0.0, 200.0), bounds=[103.5])
    assert rec({"songs": songs}, doc)["song_edge_hits"] == [True, False, False, True]


def test_a_single_tempo_misses_a_song_that_changes_it() -> None:
    songs = [
        {"start_s": 0.0, "end_s": 100.0, "bpm": 120.0},
        {"start_s": 100.0, "end_s": 200.0, "bpm": 90.0},
    ]
    doc = make_doc(duration=200.0, content=(0.0, 200.0), bounds=[100.0])
    assert rec({"songs": songs}, doc)["song_bpm_hits"] == [True, False]


def test_warnings_come_from_the_document_itself() -> None:
    assert sc.warning_count(make_doc(), 0.5) == 0
    assert sc.warning_count(make_doc(conf=0.3), 0.5) == 1
    assert sc.warning_count(make_doc(beats=[1.0, 2.0]), 0.5) == 1
    assert sc.warning_count(make_doc(content=(0.0, 30.0)), 0.5) == 1


def test_a_baseline_entry_scores_with_the_same_function() -> None:
    entry = {"bpm": 120.0, "beats": beats(), "boundaries": [30.0, 60.0], "warnings": 0}
    r = sc.score_track(truth_for(beats()), sc.estimate_from_baseline(entry), TOL)
    assert r["beat_f"] == 1.0 and r["bpm_octave_ok"] and r["warned"] is False
    assert "edge_hits" not in r and "drops" not in r  # the baseline gave none: never compared
