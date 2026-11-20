"""Per-corpus gate verdicts, the gates file's external section, and time and memory selection."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from app.quality import corpus_gates_calc as cg
from app.quality import measure_calc, runner

GATES = json.loads(runner.GATES.read_text(encoding="utf-8"))
COVERED = {"tracks": 20, "annotated": 20, "min_tracks": 20, "min_annotated": 20, "sufficient": True}


def passing(corpus: str) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {"false_exact": 0.0, "audio_kept_after_job": 0.0}
    for name in GATES["external"]["gate_sets"][corpus]:
        rule = cg.threshold(GATES, name)
        out[name] = rule["value"]
    return out


def test_every_gate_of_every_set_has_a_threshold_and_every_corpus_a_rule() -> None:
    ext = GATES["external"]
    assert set(ext["gate_sets"]) == set(ext["corpora"]) == {"cc", "spotify", "studio", "mix"}
    for names in ext["gate_sets"].values():
        for name in names:
            assert cg.threshold(GATES, name)["op"] in (">=", "<=")
    assert set(GATES["per_source"]) - {"note"} == {"spotify", "studio", "mix"}


def test_the_plan_thresholds_are_reused_not_copied() -> None:
    assert cg.threshold(GATES, "beat_f_overall") == GATES["signal"]["beat_f_overall"]
    assert cg.threshold(GATES, "song_start_end_within_2s_fraction")["value"] == 0.95
    assert cg.threshold(GATES, "max_track_seconds")["value"] == 180


@pytest.mark.parametrize("corpus", ["cc", "spotify", "studio", "mix"])
def test_values_at_the_thresholds_pass(corpus: str) -> None:
    res = cg.evaluate_corpus(GATES, corpus, passing(corpus), COVERED)
    assert res["verdict"] == "pass" and res["failed"] == [] and res["not_measured"] == []


def test_a_regression_fails_only_its_gate() -> None:
    metrics = passing("studio")
    metrics["beat_f_overall"] = 0.70
    res = cg.evaluate_corpus(GATES, "studio", metrics, COVERED)
    assert res["verdict"] == "FAIL" and res["failed"] == ["beat_f_overall"]


def test_an_unmeasured_gate_is_not_a_pass() -> None:
    metrics = passing("studio")
    metrics["drop_recall"] = None
    res = cg.evaluate_corpus(GATES, "studio", metrics, COVERED)
    assert res["verdict"] == "incomplete" and res["not_measured"] == ["drop_recall"]


def test_a_gate_missing_from_the_metrics_is_not_measured() -> None:
    metrics = passing("studio")
    del metrics["ece_bpm"]
    assert cg.evaluate_corpus(GATES, "studio", metrics, COVERED)["not_measured"] == ["ece_bpm"]


def test_a_corpus_below_its_size_rule_is_never_a_pass() -> None:
    small = dict(COVERED, tracks=3, sufficient=False)
    assert cg.evaluate_corpus(GATES, "studio", passing("studio"), small)["verdict"] == "incomplete"


def test_a_failure_wins_over_incomplete() -> None:
    metrics = passing("studio")
    metrics["boundary_f_3s"] = 0.1
    small = dict(COVERED, sufficient=False)
    assert cg.evaluate_corpus(GATES, "studio", metrics, small)["verdict"] == "FAIL"


@pytest.mark.parametrize("name", ["false_exact", "audio_kept_after_job"])
def test_a_hard_gate_fails_on_any_nonzero_value_and_is_never_unmeasured(name: str) -> None:
    metrics = passing("cc")
    metrics[name] = 1.0
    assert cg.evaluate_corpus(GATES, "cc", metrics, COVERED)["failed"] == [name]
    metrics[name] = None
    assert cg.evaluate_corpus(GATES, "cc", metrics, COVERED)["not_measured"] == [name]


def test_a_file_that_relaxes_a_hard_gate_is_refused() -> None:
    from app.quality.gates_calc import GatesError

    bad = json.loads(json.dumps(GATES))
    bad["hard"]["false_exact"] = 1
    with pytest.raises(GatesError):
        cg.evaluate_corpus(bad, "cc", passing("cc"), COVERED)


def test_the_spotify_set_has_no_drop_gate_and_the_studio_set_has() -> None:
    sets = GATES["external"]["gate_sets"]
    assert "drop_recall" not in sets["spotify"] and "drop_recall" in sets["studio"]


# ---- time and memory -------------------------------------------------------------------------

RULE = GATES["external"]["measurement"]


def run(
    duration: float, work: float = 30.0, peak: Optional[float] = 0.8, ok: bool = True
) -> Dict[str, Any]:
    return {"ok": ok, "duration_s": duration, "work_s": work, "peak_gib": peak}


def test_short_tracks_never_stand_in_for_five_minute_tracks() -> None:
    m = measure_calc.measure([run(60.0, 1.0) for _ in range(30)], RULE)
    assert m["p95_seconds_per_5min"] is None and m["peak_memory_gib"] is None
    assert m["long_tracks"] == 0.0 and m["peak_memory_gib_all"] == 0.8


def test_five_minute_tracks_are_measured_and_a_slow_one_fails_the_gate() -> None:
    fast = [run(300.0, 20.0) for _ in range(5)]
    assert measure_calc.measure(fast, RULE)["p95_seconds_per_5min"] == 20.0
    slow = fast[:4] + [run(300.0, 90.0)]
    m = measure_calc.measure(slow, RULE)
    assert (m["p95_seconds_per_5min"] or 0.0) > 60.0
    res = cg.evaluate_corpus(GATES, "studio", {**passing("studio"), **m}, COVERED)
    assert "p95_seconds_per_5min" in res["failed"]


def test_the_time_is_normalised_to_five_minutes_only_inside_the_window() -> None:
    m = measure_calc.measure([run(250.0, 25.0) for _ in range(5)], RULE)
    assert m["p95_seconds_per_5min"] == 30.0
    assert measure_calc.measure([run(230.0, 25.0) for _ in range(5)], RULE)["long_tracks"] == 0.0
    assert measure_calc.measure([run(430.0, 25.0) for _ in range(5)], RULE)["long_tracks"] == 0.0


def test_too_few_five_minute_tracks_is_not_measured() -> None:
    assert (
        measure_calc.measure([run(300.0) for _ in range(4)], RULE)["p95_seconds_per_5min"] is None
    )


def test_memory_is_the_peak_and_unmeasured_memory_is_not_a_pass() -> None:
    runs = [run(300.0, peak=0.4) for _ in range(4)] + [run(300.0, peak=1.7)]
    m = measure_calc.measure(runs, RULE)
    assert m["peak_memory_gib"] == 1.7
    assert cg.evaluate_corpus(GATES, "cc", {**passing("cc"), **m}, COVERED)["failed"] == [
        "peak_memory_gib"
    ]
    runs[0]["peak_gib"] = None
    assert measure_calc.measure(runs, RULE)["peak_memory_gib"] is None


def test_failed_jobs_do_not_count_as_measured_tracks() -> None:
    runs = [run(300.0) for _ in range(4)] + [run(300.0, ok=False)]
    assert measure_calc.measure(runs, RULE)["long_tracks"] == 4.0


def test_a_mix_is_judged_on_its_slowest_track_in_seconds() -> None:
    m = measure_calc.measure([run(1800.0, 200.0), run(1500.0, 90.0)], RULE)
    assert m["max_track_seconds"] == 200.0
    res = cg.evaluate_corpus(GATES, "mix", {**passing("mix"), **m}, COVERED)
    assert "max_track_seconds" in res["failed"]
