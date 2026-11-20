"""The pure alignment calculation (spotdown#11) on synthetic offset fixtures."""

from __future__ import annotations

import pytest

from app import alignment_calc as calc


@pytest.mark.parametrize(
    "file_s,start,end,ref,chosen,offset,residual",
    [
        # The reference is the whole file, leading silence included: offset 0.
        (200.0, 1.5, 199.0, 200.0, "untrimmed", 0.0, 0.0),
        (200.0, 1.5, 199.0, 200.8, "untrimmed", 0.0, 0.8),
        # The reference is the silence-trimmed content: reference_time = file_time - 1.5.
        (200.0, 1.5, 199.0, 197.5, "trimmed", -1.5, 0.0),
        (200.0, 3.25, 200.0, 196.5, "trimmed", -3.25, 0.25),
        # A known offset with a residual of a few seconds is still evidence, not a failure.
        (200.0, 0.0, 200.0, 203.0, "untrimmed", 0.0, 3.0),
        # A tie keeps the simpler, untrimmed hypothesis.
        (200.0, 0.0, 200.0, 200.0, "untrimmed", 0.0, 0.0),
    ],
)
def test_the_hypothesis_with_the_smallest_residual_is_chosen(
    file_s, start, end, ref, chosen, offset, residual
) -> None:
    result = calc.align(file_s, start, end, ref)
    assert result.ok and result.chosen is not None
    assert (result.chosen.name, result.chosen.offset_s, result.chosen.residual_s) == (
        chosen,
        offset,
        residual,
    )
    assert [h.name for h in result.hypotheses] == ["untrimmed", "trimmed"]


def test_the_offset_maps_file_time_to_reference_time() -> None:
    """reference_time = file_time + offset_s: the content's start is the reference's zero."""
    result = calc.align(200.0, 2.0, 199.0, 197.0)
    assert result.chosen is not None
    assert 2.0 + result.chosen.offset_s == 0.0


def test_both_hypotheses_are_recorded_with_their_residuals() -> None:
    result = calc.align(200.0, 1.5, 199.0, 197.5)
    assert [(h.name, h.offset_s, h.residual_s) for h in result.hypotheses] == [
        ("untrimmed", 0.0, 2.5),
        ("trimmed", -1.5, 0.0),
    ]


@pytest.mark.parametrize("ref", [210.0, 150.0, 0.0, -1.0])
def test_no_plausible_mapping_fails_without_a_choice(ref: float) -> None:
    result = calc.align(200.0, 1.5, 199.0, ref)
    assert not result.ok and result.chosen is None and result.confidence is None
    assert len(result.hypotheses) == 2


def test_the_residual_bound_is_inclusive() -> None:
    assert calc.align(200.0, 0.0, 200.0, 205.0).ok
    assert not calc.align(200.0, 0.0, 200.0, 205.01).ok


def test_confidence_falls_with_the_residual() -> None:
    confs = [calc.align(200.0, 0.0, 200.0, 200.0 + r).confidence for r in (0.0, 1.0, 2.5, 5.0)]
    assert confs == [1.0, 0.8, 0.5, 0.0]


def test_an_ambiguous_choice_lowers_the_confidence() -> None:
    clear = calc.align(200.0, 3.0, 199.0, 196.0)  # trimmed 0.0 against untrimmed 4.0
    close = calc.align(200.0, 0.4, 199.8, 199.6)  # trimmed 0.2 against untrimmed 0.4
    assert clear.confidence == 1.0
    assert close.chosen is not None and close.chosen.name == "trimmed"
    assert close.confidence is not None and close.confidence < 0.7


def test_equal_offsets_never_compete() -> None:
    """No leading silence: both hypotheses are the same mapping, so there is no ambiguity."""
    result = calc.align(200.0, 0.0, 199.9, 200.0)
    assert result.confidence == 1.0


def test_the_standalone_hypotheses_are_unchanged() -> None:
    assert calc.offset_hypotheses(200.0, 1.5, 197.5, 197.5) == (True, 1.5, 0.0)
    assert calc.offset_hypotheses(200.0, 1.5, 197.5, None) == (None, 1.5, None)
