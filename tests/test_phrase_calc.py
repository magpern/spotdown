"""Phrases of 8 and 16 bars from structural boundaries (pure)."""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pytest

from app.core import phrase_calc

BAR = 2.0  # 120 BPM in 4/4


def _c(bars: Sequence[float], conf: float = 0.8) -> List[Tuple[float, float]]:
    return [(b * BAR, conf) for b in bars]


def _run(bars: Sequence[float], conf: float = 0.8):
    return phrase_calc.phrases(_c(bars, conf), BAR, [])


def test_sections_that_add_up_to_8_bars_make_8_bar_phrases() -> None:
    found = _run([0, 4, 8, 16, 20, 24])
    assert [(p["start"], p["end"], p["bars"]) for p in found] == [
        (0.0, 16.0, 8),
        (16.0, 32.0, 8),
        (32.0, 48.0, 8),
    ]


def test_a_16_bar_section_is_one_16_bar_phrase() -> None:
    found = _run([0, 8, 24, 32])
    assert [p["bars"] for p in found] == [8, 16, 8]


def test_a_pickup_shifts_the_phrases_instead_of_tiling_from_the_start() -> None:
    found = _run([0, 4, 12, 20, 28])
    assert [(p["start"], p["bars"]) for p in found] == [(8.0, 8), (24.0, 8), (40.0, 8)]


def test_sections_that_are_not_whole_phrases_give_nothing() -> None:
    assert _run([0, 3, 6, 9, 12]) == []
    assert _run([0, 5, 10]) == []
    assert _run([]) == []
    assert _run([0]) == []


def test_a_boundary_within_half_a_bar_still_counts_and_a_bar_off_does_not() -> None:
    assert len(_run([0, 8.4])) == 1
    assert _run([0, 9.0]) == []


def test_times_snap_to_tracked_bar_starts() -> None:
    found = phrase_calc.phrases([(0.1, 0.8), (16.2, 0.8)], BAR, [0.0, 2.0, 16.0])
    assert [(p["start"], p["end"]) for p in found] == [(0.0, 16.0)]


def test_a_slow_or_missing_tempo_gives_nothing() -> None:
    assert phrase_calc.phrases(_c([0, 8]), 0.1, []) == []


def test_confidence_never_exceeds_the_cap_and_follows_the_boundaries() -> None:
    assert phrase_calc.CAP <= 0.4  # raise only with held-out evidence (spotdown#20)
    high = _run([0, 8], conf=0.95)[0]["confidence"]
    low = _run([0, 8], conf=0.3)[0]["confidence"]
    assert high == phrase_calc.CAP and 0 < low < high


@pytest.mark.parametrize("conf", [0.0, 0.5, 1.0])
def test_confidence_is_in_range(conf: float) -> None:
    assert 0.0 <= _run([0, 8], conf=conf)[0]["confidence"] <= phrase_calc.CAP
