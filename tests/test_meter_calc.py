"""The pure meter and downbeat rule: hand-made beats and accents, no audio."""

from __future__ import annotations

import random
from typing import List, Tuple

import pytest

from app.core import meter_calc as mc

BEAT = 0.5


def _track(
    pattern: List[float], bars: int, noise: float = 0.03, lead: int = 0
) -> Tuple[List[float], List[float]]:
    """Beat times every 0.5 s and the accent of each, ``lead`` beats before the first bar."""
    rng = random.Random(7)
    accents = [0.5] * lead + [v for _ in range(bars) for v in pattern]
    return [i * BEAT for i in range(len(accents))], [
        a + rng.uniform(-noise, noise) for a in accents
    ]


@pytest.mark.parametrize(
    ("pattern", "meter"),
    [([1.0, 0.4, 0.6, 0.4], 4), ([1.0, 0.4, 0.4], 3), ([0.5, 1.0, 0.4, 0.9], 4)],
)
def test_the_meter_is_the_period_of_the_accent(pattern: List[float], meter: int) -> None:
    beats, acc = _track(pattern, 12)
    got = mc.estimate(beats, acc, [0.0])
    assert got is not None and got["beats_per_bar"] == meter


def test_downbeats_step_one_bar_from_the_anchor_and_restart_at_the_next_anchor() -> None:
    beats, acc = _track([1.0, 0.3, 0.3, 0.3], 14)
    got = mc.estimate(beats, acc, [0.0, 14.0])  # 7 bars on, an anchor: the grid restarts there
    assert got is not None and got["beats_per_bar"] == 4
    starts = [b["start"] for b in got["bars"]]
    assert starts[:3] == [0.0, 2.0, 4.0] and 14.0 in starts
    shifted = mc.estimate(beats, acc, [0.0, 13.0])  # 6.5 bars: the next anchor restarts mid-grid
    assert shifted is not None and 13.0 in [b["start"] for b in shifted["bars"]]


def test_anchors_off_the_grid_lower_the_bar_confidence() -> None:
    beats, acc = _track([1.0, 0.7, 0.85, 0.7], 14, noise=0.2)  # a weak accent: below the cap
    agree = mc.estimate(beats, acc, [0.0, 14.0])
    off = mc.estimate(beats, acc, [0.0, 13.0])
    assert agree is not None and off is not None
    assert off["bars"][0]["confidence"] < agree["bars"][0]["confidence"]


def test_no_accent_pattern_claims_nothing() -> None:
    assert mc.estimate(*_track([0.7], 40, noise=0.0), [0.0]) is None  # flat
    beats, acc = _track([1.0, 0.3, 0.3], 3)
    assert mc.estimate(beats, acc, [0.0]) is None  # fewer than the minimum beats
    rng = random.Random(3)
    beats = [i * BEAT for i in range(80)]
    assert mc.estimate(beats, [rng.random() for _ in beats], [0.0]) is None  # pure noise
    assert mc.estimate(*_track([1.0, 0.3, 0.3], 12), []) is None  # no anchor, no downbeat


def test_confidence_never_exceeds_the_cap_while_the_gate_is_not_passed() -> None:
    assert mc.GATE_PASSED is False and mc.CAP == 0.4
    beats, acc = _track([1.0, 0.0, 0.0, 0.0], 30, noise=0.0)
    got = mc.estimate(beats, acc, [0.0])
    assert got is not None
    assert got["confidence"] <= mc.CAP and all(b["confidence"] <= mc.CAP for b in got["bars"])
