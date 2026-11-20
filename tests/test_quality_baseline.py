"""Baseline comparison: per-source rows against derived baseline results (plan tolerances).

A row passes at exactly the tolerance and fails beyond it (each case mutates the margin)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from app.quality import baseline_calc as bc
from app.quality import corpus_scoring_calc as sc
from app.quality import runner
from app.quality.corpus_calc import CorpusError
from tests.corpus_helpers import TOL, make_doc, truth_for

GATES = json.loads(runner.GATES.read_text(encoding="utf-8"))
STUDIO = GATES["per_source"]["studio"]
BEATS = [round(i * 0.5, 4) for i in range(200)]


def tracks(n: int) -> List[Dict[str, Any]]:
    return [{"id": f"t{i}", "truth": truth_for(BEATS)} for i in range(n)]


def sd(n: int, off: Sequence[int] = ()) -> Dict[str, Dict[str, Any]]:
    """spotdown estimates: tracks in ``off`` have beats 0.3 s late (beat F 0)."""
    return {
        f"t{i}": sc.estimate_from_doc(
            make_doc(beats=[t + 0.3 for t in BEATS] if i in off else BEATS), TOL
        )
        for i in range(n)
    }


def base(n: int, off: Sequence[int] = ()) -> Dict[str, Dict[str, Any]]:
    return {
        f"t{i}": {
            "bpm": 120.0,
            "beats": [t + 0.3 for t in BEATS] if i in off else BEATS,
            "boundaries": [30.0, 60.0],
        }
        for i in range(n)
    }


def row(cmp: Dict[str, Any], metric: str) -> Dict[str, Any]:
    return next(r for r in cmp["rows"] if r["metric"] == metric)


def test_equal_to_the_baseline_passes_every_studio_row() -> None:
    cmp = bc.compare(STUDIO, tracks(10), sd(10), base(10), TOL)
    assert [r["status"] for r in cmp["rows"]] == ["pass"] * 3 and cmp["common_tracks"] == 10


def test_beat_f_exactly_at_baseline_minus_the_tolerance_passes_and_below_it_fails() -> None:
    # 50 tracks: 1 of 50 off gives beat F 0.98 against a baseline of 1.0, exactly the tolerance.
    at = bc.compare(STUDIO, tracks(50), sd(50, [0]), base(50), TOL)
    assert row(at, "beat_f_overall")["spotdown"] == 0.98
    assert row(at, "beat_f_overall")["status"] == "pass"
    beyond = bc.compare(STUDIO, tracks(50), sd(50, [0, 1]), base(50), TOL)
    assert row(beyond, "beat_f_overall")["status"] == "FAIL"


def test_a_better_spotdown_never_fails() -> None:
    cmp = bc.compare(STUDIO, tracks(10), sd(10), base(10, [0, 1, 2]), TOL)
    assert row(cmp, "beat_f_overall")["status"] == "pass"


def test_the_bpm_row_allows_one_point_for_studio_tracks() -> None:
    trs = tracks(100)
    mine = sd(100)
    wrong = sc.estimate_from_doc(make_doc(bpm=140.0), TOL)
    mine["t0"] = wrong
    cmp_one = bc.compare(STUDIO, trs, mine, base(100), TOL)
    assert row(cmp_one, "bpm_within_2pct_fraction")["status"] == "pass"  # 0.99 against 1.0
    mine["t1"] = wrong
    cmp_two = bc.compare(STUDIO, trs, mine, base(100), TOL)
    assert row(cmp_two, "bpm_within_2pct_fraction")["status"] == "FAIL"


def test_a_row_resting_on_too_few_tracks_is_not_measured() -> None:
    cmp = bc.compare(STUDIO, tracks(4), sd(4), base(4), TOL)
    assert {r["status"] for r in cmp["rows"]} == {"not_measured"}


def test_only_tracks_where_both_have_a_value_are_compared() -> None:
    cmp = bc.compare(STUDIO, tracks(10), sd(8), base(9), TOL)
    assert cmp["common_tracks"] == 8 and cmp["missing_baseline"] == []
    cmp2 = bc.compare(STUDIO, tracks(10), sd(10), base(7), TOL)
    assert cmp2["common_tracks"] == 7 and cmp2["missing_baseline"] == ["t7", "t8", "t9"]


def test_a_metric_the_baseline_does_not_provide_is_not_measured() -> None:
    entries = base(10)
    for e in entries.values():
        del e["beats"]
    cmp = bc.compare(STUDIO, tracks(10), sd(10), entries, TOL)
    assert row(cmp, "beat_f_overall")["status"] == "not_measured"


def test_warnings_are_compared_with_a_five_point_allowance_in_the_other_direction() -> None:
    rule = GATES["per_source"]["spotify"]
    trs = tracks(20)
    entries = {tid: dict(e, warnings=0) for tid, e in base(20).items()}
    low = {
        tid: sc.estimate_from_doc(make_doc(conf=0.3 if i == 0 else 0.9), TOL)
        for i, tid in enumerate(entries)
    }
    assert (
        row(bc.compare(rule, trs, low, entries, TOL), "warning_fraction")["status"] == "pass"
    )  # 0.05
    low["t1"] = sc.estimate_from_doc(make_doc(conf=0.3), TOL)
    assert row(bc.compare(rule, trs, low, entries, TOL), "warning_fraction")["status"] == "FAIL"


def test_the_mix_rows_demand_at_least_the_baseline() -> None:
    rule = GATES["per_source"]["mix"]
    songs = [{"start_s": 0.0, "end_s": 100.0, "bpm": 120.0}]
    trs = [{"id": f"m{i}", "truth": {"songs": songs}} for i in range(6)]
    good = sc.estimate_from_doc(make_doc(content=(0.0, 100.0)), TOL)
    bad = sc.estimate_from_doc(make_doc(content=(10.0, 80.0), bounds=[]), TOL)
    mine = {f"m{i}": good for i in range(6)}
    entries = {f"m{i}": {"songs": songs} for i in range(6)}
    assert {r["status"] for r in bc.compare(rule, trs, mine, entries, TOL)["rows"]} == {"pass"}
    mine["m0"] = bad
    cmp = bc.compare(rule, trs, mine, entries, TOL)
    assert row(cmp, "song_start_end_within_2s_fraction")["status"] == "FAIL"


def test_unannotated_tracks_report_agreement_with_the_baseline_only() -> None:
    trs = [{"id": f"t{i}", "truth": {}} for i in range(10)]
    cmp = bc.compare(STUDIO, trs, sd(10), base(10), TOL)
    assert cmp["bpm_agreement_with_baseline_reported"] == 1.0
    assert {r["status"] for r in cmp["rows"]} == {"not_measured"}


def valid() -> Dict[str, Any]:
    return {
        "baseline_version": 1,
        "source": "mowl.music.analyze_track",
        "tracks": {"a": {"bpm": 120}},
    }


def test_a_baseline_file_is_validated() -> None:
    assert bc.validate_baseline(valid()) == {"a": {"bpm": 120}}
    bad: List[Tuple[str, Any]] = [
        ("baseline_version", 2),
        ("source", ""),
        ("tracks", {"a": {"tempo": 120}}),
        ("tracks", {"a": {"bpm": -1}}),
        ("tracks", {"a": {"beats": [1, "x"]}}),
        ("tracks", {"a": {"songs": [{"start_s": 1}]}}),
    ]
    for key, value in bad:
        with pytest.raises(CorpusError):
            bc.validate_baseline(dict(valid(), **{key: value}))
