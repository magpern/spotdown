"""The pure metrics: each against a hand-computed case."""

from __future__ import annotations

import pytest

from app.quality import metrics_calc as m


def test_prf_empty_cases() -> None:
    assert m.prf([], [], 0.1) == (1.0, 1.0, 1.0)
    assert m.prf([1.0], [], 0.1) == (0.0, 0.0, 0.0)
    assert m.prf([], [1.0], 0.1) == (0.0, 0.0, 0.0)


def test_matching_is_one_to_one() -> None:
    assert m.match_count([1.0, 1.05], [1.02], 0.1) == 1
    p, r, f = m.prf([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 9.0], 0.07)
    assert (p, r) == (pytest.approx(2 / 3), 0.5)
    assert f == pytest.approx(2 * p * r / (p + r))


def test_beat_window_is_70_ms() -> None:
    ref = [6.0, 6.5, 7.0]
    assert m.beat_f(ref, [6.06, 6.56, 7.06]) == 1.0
    assert m.beat_f(ref, [6.08, 6.58, 7.08]) == 0.0
    assert m.beat_f([1.0, 2.0], []) == 1.0


def test_boundary_ignores_the_file_ends_and_uses_3_s() -> None:
    assert m.boundary_f([0.1, 30.0, 59.9], [29.0], 60.0) == 1.0
    assert m.boundary_f([30.0], [33.5], 60.0) == 0.0


def test_bpm_is_octave_aware_but_reports_the_exact_octave() -> None:
    assert m.bpm_errors(120.0, 121.0) == (True, True)
    assert m.bpm_errors(120.0, 60.0) == (True, False)
    assert m.bpm_errors(120.0, 240.0) == (True, False)
    assert m.bpm_errors(120.0, 126.0) == (False, False)
    assert m.bpm_errors(120.0, 0.0) == (False, False)


def test_edges_and_drops() -> None:
    assert m.edge_hits([1.0, 50.0], [1.2, 50.3]) == [True, False]
    assert m.drop_counts([10.0, 30.0], [10.4, 50.0], 0.5) == (1, 1, 1)


def test_ece_is_zero_when_calibrated_and_large_when_not() -> None:
    assert m.ece([], []) == 0.0
    assert m.ece([1.0] * 4, [True] * 4) == pytest.approx(0.0)
    assert m.ece([0.95] * 10, [True] * 5 + [False] * 5) == pytest.approx(0.45)
    assert m.ece([0.5, 0.5], [True, False]) == pytest.approx(0.0)


def test_percentile() -> None:
    assert m.percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)


def test_ece_weights_each_bin_by_its_share_of_the_samples() -> None:
    # 9 samples at 0.95 that are all right (gap 0.05) and 1 at 0.15 that is right (gap 0.85):
    # 0.9 * 0.05 + 0.1 * 0.85 = 0.13; an unweighted mean of the two bins would be 0.45.
    conf = [0.95] * 9 + [0.15]
    assert m.ece(conf, [True] * 10) == pytest.approx(0.13)


def test_bpm_tolerance_is_a_parameter() -> None:
    assert m.bpm_errors(120.0, 123.0) == (False, False)
    assert m.bpm_errors(120.0, 123.0, tol=0.03) == (True, True)
