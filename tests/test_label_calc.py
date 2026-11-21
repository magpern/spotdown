"""Section labels from segment structure (pure): rules, confidence and the pre-gate cap."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import pytest

from app.core import label_calc

Seg = Tuple[float, float, float, int]  # start, end, energy_struct, group


def _segs(rows: Sequence[Seg]) -> List[Dict[str, Any]]:
    return [{"start": a, "end": b, "energy_struct": e, "group": g} for a, b, e, g in rows]


def _labels(rows: Sequence[Seg], drops: Sequence[float] = (), cs: float = 0.0, ce: float = 100.0):
    return [x["label"] for x in label_calc.label_segments(_segs(rows), drops, cs, ce)]


CLUB: List[Seg] = [  # intro, build, drop, breakdown, build, drop, outro
    (0, 10, 0.3, 3),
    (10, 20, 0.15, 1),
    (20, 40, 0.4, 2),
    (40, 50, 0.01, 4),
    (50, 60, 0.15, 1),
    (60, 80, 0.4, 2),
    (80, 90, 0.3, 3),
]
SONG: List[Seg] = [  # intro, verse, chorus, bridge, chorus
    (0, 10, 0.2, 4),
    (10, 20, 0.4, 2),
    (20, 30, 0.5, 1),
    (30, 40, 0.35, 3),
    (40, 50, 0.5, 1),
]


def test_a_club_track_reads_intro_verse_chorus_breakdown() -> None:
    assert _labels(CLUB, drops=[20.0, 60.0]) == [
        "intro", "verse", "chorus", "breakdown", "verse", "chorus", "outro",
    ]  # fmt: skip


def test_a_song_without_drops_finds_its_chorus_by_repetition_and_a_bridge_between() -> None:
    assert _labels(SONG) == ["intro", "verse", "chorus", "bridge", "chorus"]


def test_a_final_chorus_is_not_an_outro() -> None:
    assert _labels(SONG)[-1] == "chorus"


def test_silence_around_the_content_is_not_labelled() -> None:
    rows: List[Seg] = [
        (0, 2, 0.0, 9),
        *[(a + 2, b + 2, e, g) for a, b, e, g in SONG],
        (52, 55, 0.0, 9),
    ]
    found = label_calc.label_segments(_segs(rows), [], 2.0, 52.0)
    assert [x["index"] for x in found] == [1, 2, 3, 4, 5]
    assert found[0]["label"] == "intro"


@pytest.mark.parametrize("rows", [[], [(0, 30, 0.4, 1)]])
def test_too_little_structure_gives_no_labels(rows: List[Seg]) -> None:
    assert _labels(rows) == []


def test_every_label_is_in_the_schema_vocabulary() -> None:
    for rows, drops in ((CLUB, [20.0, 60.0]), (SONG, [])):
        assert set(_labels(rows, drops)) <= set(label_calc.LABELS)


def test_confidence_is_capped_until_the_gate_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label_calc, "GATE_PASSED", False)
    low = label_calc.label_segments(_segs(CLUB), [20.0, 60.0], 0.0, 100.0)
    assert max(x["confidence"] for x in low) <= label_calc.UNGATED_CAP
    monkeypatch.setattr(label_calc, "GATE_PASSED", True)
    high = label_calc.label_segments(_segs(CLUB), [20.0, 60.0], 0.0, 100.0)
    assert label_calc.UNGATED_CAP < max(x["confidence"] for x in high) <= label_calc.GATED_CAP
    assert [x["label"] for x in low] == [x["label"] for x in high]


def test_the_label_gate_is_not_passed_without_held_out_evidence() -> None:
    # The synthetic corpus is the one the rules were written on (spotdown#20 holds the real
    # evidence), so passing its gates never raises the cap. Change this only with held-out gates.
    assert label_calc.GATE_PASSED is False
    found = label_calc.label_segments(_segs(CLUB), [20.0, 60.0], 0.0, 100.0)
    assert found and max(x["confidence"] for x in found) <= 0.4


def test_a_chorus_with_both_cues_is_more_confident_than_one_with_a_single_cue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(label_calc, "GATE_PASSED", True)
    both = label_calc.label_segments(_segs(CLUB), [20.0, 60.0], 0.0, 100.0)
    one = label_calc.label_segments(_segs(CLUB), [20.0], 0.0, 100.0)  # second drop undetected

    def chorus(found: List[Dict[str, Any]], index: int) -> float:
        return next(
            x["confidence"] for x in found if x["index"] == index and x["label"] == "chorus"
        )

    assert chorus(both, 5) > chorus(one, 5) or chorus(both, 5) == label_calc.GATED_CAP
    assert chorus(one, 2) <= chorus(both, 2)


def test_a_short_segment_carries_less_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label_calc, "GATE_PASSED", True)
    rows: List[Seg] = [(0, 10, 0.2, 4), (10, 12, 0.4, 2), (12, 22, 0.5, 1), (22, 32, 0.5, 1)]
    found = label_calc.label_segments(_segs(rows), [], 0.0, 100.0)
    assert found[1]["confidence"] < label_calc.BASE_CONFIDENCE[found[1]["label"]]


STEADY: List[
    Seg
] = [  # intro, three verses (the repeating group), two choruses, outro: same loudness
    (0, 8, 0.82, 4),
    (8, 32, 0.84, 1),
    (32, 44, 0.83, 2),
    (44, 68, 0.84, 1),
    (68, 80, 0.83, 2),
    (80, 104, 0.84, 1),
    (104, 112, 0.82, 4),
]


def test_a_steady_track_has_no_loudest_group_chorus() -> None:
    """The loudest repeating group is the verse by a hair; it is no evidence of a chorus."""
    labels = _labels(STEADY, ce=112.0)
    assert "chorus" not in labels
    assert labels[0] == "intro" and labels[-1] == "outro" and labels.count("verse") == 5


def test_the_peak_margin_decides_between_steady_and_clear() -> None:
    clear = [(a, b, e + 0.2 if g == 1 else e, g) for a, b, e, g in STEADY]
    assert (
        _labels(clear, ce=112.0).count("chorus") == 3
    )  # the same arrangement, the group clearly louder
    just_under = [(a, b, e + 0.07 if g == 1 else e, g) for a, b, e, g in STEADY]
    assert "chorus" not in _labels(
        just_under, ce=112.0
    )  # 0.01 + 0.07 is below label_calc.PEAK_MARGIN


def test_the_old_rule_made_the_steady_verses_chorus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The margin is what changed: with it off (the rule before spotdown#47) three of seven are
    chorus, so the check above can fail."""
    monkeypatch.setattr(label_calc, "PEAK_MARGIN", -1.0)
    assert _labels(STEADY, ce=112.0).count("chorus") == 3


def test_a_drop_still_makes_a_chorus_on_a_steady_track() -> None:
    labels = _labels(STEADY, drops=[32.0], ce=112.0)
    assert labels[2] == "chorus" and labels.count("chorus") == 1


def test_a_group_that_is_most_of_the_track_has_nothing_to_compare_with() -> None:
    rows: List[Seg] = [
        (0, 10, 0.2, 4),
        (10, 20, 0.5, 1),
        (20, 30, 0.5, 1),
        (30, 40, 0.5, 1),
        (40, 50, 0.2, 4),
    ]
    assert "chorus" not in _labels(rows, ce=50.0)  # 30 of 50 s: no margin, and too much of it


def test_a_steady_track_falls_back_to_the_most_repeated_group_when_it_is_a_minority() -> None:
    """Same loudness everywhere, but one group repeats most and covers under half the track."""
    rows: List[Seg] = [
        (0, 8, 0.8, 4),
        (8, 20, 0.8, 2),
        (20, 28, 0.8, 1),
        (28, 40, 0.8, 3),
        (40, 48, 0.8, 1),
        (48, 60, 0.8, 5),
        (60, 68, 0.8, 1),
        (68, 76, 0.8, 4),
    ]
    assert _labels(rows, ce=76.0) == [
        "intro", "verse", "chorus", "bridge", "chorus", "bridge", "chorus", "outro",
    ]  # fmt: skip


def test_a_tie_between_repeated_groups_makes_no_chorus() -> None:
    rows: List[Seg] = [
        (0, 8, 0.8, 4),
        (8, 16, 0.8, 1),
        (16, 24, 0.8, 2),
        (24, 32, 0.8, 1),
        (32, 40, 0.8, 2),
        (40, 48, 0.8, 4),
    ]
    assert "chorus" not in _labels(rows, ce=48.0)


def test_steady_labels_keep_the_confidence_cap() -> None:
    found = label_calc.label_segments(_segs(STEADY), [32.0], 0.0, 112.0)
    assert found and max(x["confidence"] for x in found) <= label_calc.UNGATED_CAP
