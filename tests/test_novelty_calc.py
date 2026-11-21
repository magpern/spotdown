"""Novelty peaks and the energy contrast that sets their threshold (pure)."""

from __future__ import annotations

import numpy as np

from app.core import novelty_calc


def test_energy_contrast_is_the_spread_of_block_means_inside_the_range() -> None:
    steady = np.full(40, 0.8)
    swing = np.tile(np.repeat([0.1, 0.9], 4), 5)  # 4 s quiet, 4 s loud
    assert novelty_calc.energy_contrast(steady, 1.0, 0.0, 40.0) == 0.0
    assert novelty_calc.energy_contrast(swing, 1.0, 0.0, 40.0) > 0.7
    assert novelty_calc.energy_contrast(swing, 1.0, 0.0, 6.0) == 0.0  # under two blocks
    # silence outside the content range does not count as contrast
    padded = np.concatenate([np.zeros(10), steady, np.zeros(10)])
    assert novelty_calc.energy_contrast(padded, 1.0, 10.0, 50.0) == 0.0


def test_a_low_contrast_track_uses_the_lower_peak_share() -> None:
    assert novelty_calc.peak_rel(0.05) == novelty_calc.PEAK_REL_LOW
    assert novelty_calc.peak_rel(0.5) == novelty_calc.PEAK_REL
    assert novelty_calc.PEAK_REL_LOW < novelty_calc.PEAK_REL


def test_the_lower_share_keeps_a_weak_peak_that_the_default_drops() -> None:
    nov = np.zeros(60)
    nov[10], nov[40] = 1.0, 0.2  # the weak one is 20% of the strongest
    assert novelty_calc.pick_peaks(nov, 0.5) == [10]
    assert novelty_calc.pick_peaks(nov, 0.5, rel=novelty_calc.PEAK_REL_LOW) == [10, 40]


def test_a_flat_curve_still_yields_no_boundary_at_the_lower_share() -> None:
    nov = np.full(60, 0.01)  # below the absolute floor
    assert novelty_calc.pick_peaks(nov, 0.5, rel=novelty_calc.PEAK_REL_LOW) == []
