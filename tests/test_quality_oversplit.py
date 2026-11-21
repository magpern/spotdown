"""The over-split check (spotdown#46): sections with in-section variation are not cut into parts.

The two ``oversplit`` fixtures carry fills, a stop and a timbre change inside verses and choruses;
the truth has boundaries only at the real section changes. Before the merge step the analysis cut
them into more than the arrangement plus one segment; with it, not.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.core import merge_calc, segments
from app.core.analyser import analyse_signal
from app.quality import specs, synth

OVERSPLIT = [s for s in specs.SPECS if s.get("corpus") == specs.OVERSPLIT]


def _analyse(spec: Dict[str, Any]) -> Dict[str, Any]:
    y, truth = synth.render(spec)
    return {"result": analyse_signal(y.astype("float64"), synth.SR), "truth": truth}


def _starts(result: Dict[str, Any]) -> List[float]:
    return [s["start"] for s in result["segments"]]


def test_the_truth_has_boundaries_only_at_the_section_changes() -> None:
    assert len(OVERSPLIT) == 2
    for spec in OVERSPLIT:
        truth = synth.render(spec)[1]
        assert len(truth["labels"]) == 6 and len(truth["boundaries"]) == 5
        assert [x["label"] for x in truth["labels"]] == [
            "intro", "verse", "chorus", "verse", "chorus", "outro"
        ]  # fmt: skip
        assert truth["labels"][-1]["end"] == truth["duration_s"]


@pytest.mark.parametrize("spec", OVERSPLIT, ids=lambda s: s["id"])
def test_the_merge_step_keeps_the_segments_to_the_truth_sections_plus_one(
    spec: Dict[str, Any],
) -> None:
    run = _analyse(spec)
    assert len(run["result"]["segments"]) <= len(run["truth"]["labels"]) + 1
    assert all(d in _starts(run["result"]) for d in run["result"]["drops"])  # drops stay exact


@pytest.mark.parametrize("spec", OVERSPLIT, ids=lambda s: s["id"])
def test_without_the_merge_step_the_same_audio_is_over_split(
    spec: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check bites: with both merge passes switched off (the code before this change) the
    analysis makes more than the truth sections plus one."""
    monkeypatch.setattr(merge_calc, "merge_short", lambda t, n, k, b: (list(t), list(n)))
    monkeypatch.setattr(merge_calc, "merge_similar", lambda t, n, f, k: (list(t), list(n)))
    run = _analyse(spec)
    assert len(run["result"]["segments"]) > len(run["truth"]["labels"]) + 1


def test_a_drop_and_a_content_edge_survive_next_to_a_short_segment() -> None:
    """Through ``segments.segments``: a 1 bar piece beside a drop and one beside a content edge."""
    spec = specs.SPECS[4]  # edm-128, with a drop after the 4-bar intro and build
    y, _ = synth.render(spec)
    sr = synth.SR
    res = analyse_signal(y.astype("float64"), sr)
    drop = res["drops"][0]
    starts = _starts(res)
    assert drop in starts
    # the same segmentation with a drop and an edge announced 1.5 s from a cut: both stay
    short = [0.0, drop - 1.5, drop, res["duration_s"] / 2, res["duration_s"]]
    keep = merge_calc.protected_times(short, [drop, short[3]], segments.MERGE_S)
    kept, _ = merge_calc.merge_short(short, [1.0, 0.2, 0.2, 0.2], keep, 4 * 60 / res["bpm"])
    assert drop in kept and short[3] in kept and (drop - 1.5) not in kept
