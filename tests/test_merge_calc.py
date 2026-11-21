"""Merging over-fine segments (pure): the short rule, the similarity rule and what never merges."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from app.core import merge_calc

BAR = 2.0  # 120 bpm in 4/4; the short threshold is 2.5 bars = 5 s


def _short(
    times: Sequence[float], nov: Sequence[float], keep: Sequence[float] = (), bar: float = BAR
) -> List[float]:
    return merge_calc.merge_short(times, nov, keep, bar)[0]


def _similar(
    times: Sequence[float], nov: Sequence[float], feats: Sequence[Sequence[float]], keep=()
) -> Tuple[List[float], List[float]]:
    return merge_calc.merge_similar(times, nov, [np.array(f, dtype=float) for f in feats], keep)


def test_a_segment_under_the_bar_threshold_merges_across_its_weaker_boundary() -> None:
    # segments 0-20, 20-24 (2 bars), 24-50: the short one joins the side with less novelty
    assert _short([0, 20, 24, 50], [1.0, 0.3, 0.2]) == [0, 20, 50]
    assert _short([0, 20, 24, 50], [1.0, 0.2, 0.3]) == [0, 24, 50]


def test_a_segment_at_the_threshold_stays_and_a_strong_boundary_keeps_a_short_one() -> None:
    assert _short([0, 20, 25, 50], [1.0, 0.3, 0.3]) == [0, 20, 25, 50]  # 2.5 bars
    assert _short([0, 20, 24, 50], [1.0, 0.7, 0.65]) == [0, 20, 24, 50]  # clearly separate


def test_without_a_tempo_a_floor_in_seconds_applies() -> None:
    kept = _short([0, 20, 25, 50], [1.0, 0.3, 0.2], bar=0.0)
    assert 25 not in kept and 20 in kept  # 5 s < 6 s, merged across the weaker boundary (25)
    assert _short([0, 20, 27, 50], [1.0, 0.3, 0.2], bar=0.0) == [0, 20, 27, 50]  # 7 s stays


def test_a_drop_or_a_content_edge_survives_even_next_to_a_short_segment() -> None:
    times, nov = [0.0, 20.0, 24.0, 50.0], [1.0, 0.2, 0.3]
    keep = merge_calc.protected_times(times, [20.4], near_s=3.0)  # a drop near the boundary at 20
    assert keep == [20.0]
    assert _short(times, nov, keep) == [0.0, 20.0, 50.0]  # the other boundary goes instead
    both = merge_calc.protected_times(times, [20.4, 24.0], near_s=3.0)
    assert _short(times, nov, both) == times  # nothing to remove: both are protected
    far = merge_calc.protected_times(times, [35.0], near_s=3.0)
    assert far == []  # no boundary stands for a time that is not near one


def test_a_variation_between_two_similar_neighbours_merges_and_a_real_change_does_not() -> None:
    same = [[1.0, 0.0], [0.9, 0.1], [1.0, 0.1]]
    assert _similar([0, 20, 40, 60], [1.0, 0.3, 0.3], same)[0] == [0, 60]
    other = [[1.0, 0.0], [0.0, 1.0]]
    assert _similar([0, 20, 40], [1.0, 0.3], other)[0] == [0, 20, 40]
    assert _similar([0, 20, 40], [1.0, 0.5], [[1.0, 0.0], [0.9, 0.1]])[0] == [0, 20, 40]


def test_a_merged_segment_is_compared_by_its_weighted_mean() -> None:
    feats = [[1.0, 0.0], [1.0, 0.1], [0.0, 1.0]]
    times, nov = _similar([0, 20, 40, 60], [1.0, 0.1, 0.3], feats)
    assert times == [0, 40, 60] and len(nov) == 2  # the third part is a different sound


def test_the_result_is_deterministic_and_the_weakest_boundary_goes_first() -> None:
    feats = [[1.0, 0.0]] * 3
    first = _similar([0, 20, 40, 60], [1.0, 0.3, 0.2], feats)
    assert first == _similar([0, 20, 40, 60], [1.0, 0.3, 0.2], feats)
    assert first[0] == [0, 60]


def test_a_short_segment_beside_a_protected_boundary_merges_only_across_a_weak_one() -> None:
    times = [0.0, 20.0, 40.0, 44.0, 60.0]  # a 2 bar piece between 40 and 44, 44 is an edge
    keep = [44.0]
    assert 40.0 not in _short(times, [1.0, 0.3, 0.3, 0.3], keep)  # weak: a variation
    assert 40.0 in _short(times, [1.0, 0.3, 0.45, 0.3], keep)  # not weak: kept as real
    assert 44.0 not in _short(times, [1.0, 0.3, 0.45, 0.3], [])  # nothing tied: the weaker goes


def test_equal_novelty_removes_the_earlier_boundary_first() -> None:
    # three 4 s pieces (2 bars) with equal novelty: the earlier boundary goes first, which leaves
    # [24, 28] short and removes 24 too; the last-wins order would give [0, 20, 50]
    assert _short([0, 20, 24, 28, 50], [1.0, 0.2, 0.2, 0.2]) == [0, 28, 50]


def test_a_merged_vector_is_the_duration_weighted_mean_of_its_parts() -> None:
    # A (30 s) and B (10 s) merge first; the weighted mean [0.95, 0.15] is close enough to C
    # (cosine 0.78) to merge, the plain mean [0.9, 0.3] is not (0.66)
    a, b, c = [1.0, 0.0], [0.8, 0.6], [0.866, -0.5]
    times, _ = _similar([0, 30, 40, 50], [1.0, 0.1, 0.3], [a, b, c])
    assert times == [0, 50]
