"""The over-split check (spotdown#46): sections with in-section variation are not cut into parts.

The two ``oversplit`` fixtures carry fills and timbre changes inside verses and choruses;
the truth has boundaries only at the real section changes. Before the merge step the analysis cut
them into more than the arrangement plus one segment; with it, not.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pytest

from app.core import analyser, merge_calc
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
    assert not run["result"]["drops"]  # these fixtures have no drop: protection is tested below


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


def _spec(sid: str) -> Dict[str, Any]:
    return next(s for s in specs.SPECS if s["id"] == sid)


def _greedy_short(
    times: Sequence[float], novelty: Sequence[float], keep: Sequence[float], bar_s: float
) -> Tuple[List[float], List[float]]:
    """A merge that removes every boundary it is allowed to: only ``keep`` survives."""
    idx = [0] + [i for i in range(1, len(times) - 1) if times[i] in keep]
    return [times[i] for i in idx] + [times[-1]], [novelty[i] for i in idx]


def test_the_real_path_hands_every_drop_and_edge_to_the_merge_as_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``segments.segments`` protects the drops, the content edges and the quiet-region edges."""
    seen: List[Sequence[float]] = []
    real = merge_calc.protected_times

    def spy(times: Sequence[float], protected: Sequence[float], near_s: float) -> List[float]:
        seen.append(list(protected))
        return real(times, protected, near_s)

    monkeypatch.setattr(merge_calc, "protected_times", spy)
    run = _analyse(_spec("silence-edges-130"))["result"]
    assert (
        run["drops"] and run["content_start_s"] > 1.0 and run["content_end_s"] < run["duration_s"]
    )
    assert len(seen) == 1
    for t in [*run["drops"], run["content_start_s"], run["content_end_s"]]:
        assert t in seen[0]
    assert len(seen[0]) > len(run["drops"]) + 2  # and the quiet-region edges


def test_a_drop_and_the_content_edges_survive_a_merge_that_removes_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``analyse_signal`` and ``segments.segments``: with a merge that removes every
    boundary it may, a boundary stands for each drop (at its exact time) and each content edge."""
    monkeypatch.setattr(merge_calc, "merge_similar", lambda t, n, f, k: (list(t), list(n)))
    monkeypatch.setattr(merge_calc, "merge_short", _greedy_short)
    run = _analyse(_spec("silence-edges-130"))["result"]
    starts = _starts(run)
    assert len(starts) < 9  # the greedy merge did remove boundaries (9 are cut)
    for drop in run["drops"]:
        assert drop in starts  # a drop time stays exact
    for edge in (run["content_start_s"], run["content_end_s"]):
        assert min(abs(t - edge) for t in starts[1:]) < 0.5


def test_the_bar_length_comes_from_the_meter_and_is_zero_without_a_tempo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3/4 track has 3 beats a bar, a 4/4 track 4, and no tempo gives 0 (a floor in seconds)."""
    seen: List[float] = []
    real = analyser.segments

    def spy(*args: Any) -> Any:
        seen.append(args[-1])
        return real(*args)

    monkeypatch.setattr(analyser, "segments", spy)
    waltz = _analyse(_spec("waltz-kick1-96"))["result"]
    assert waltz["meter"]["beats_per_bar"] == 3
    assert seen[-1] == pytest.approx(3 * 60.0 / waltz["bpm"])
    club = _analyse(_spec("edm-128"))["result"]
    assert club["meter"]["beats_per_bar"] == 4
    assert seen[-1] == pytest.approx(4 * 60.0 / club["bpm"])
    quiet = analyse_signal(np.zeros(synth.SR * 12) + 1e-5, synth.SR)
    assert quiet["bpm"] == 0 and seen[-1] == 0.0


def _bad_record(corpus: str) -> Dict[str, Any]:
    from tests.test_quality_phrases import _record

    bad = _record(corpus, [(0.4, False)], 3)
    bad.update(
        boundary_f=0.0,
        beat_f=0.0,
        bpm_octave_ok=False,
        downbeat_est=5,
        downbeat_truth=5,
        downbeat_outcomes=[(0.3, False)] * 5,
        meter_ok=False,
        label_frames=[0, 10],
        n_segments=14,
        n_truth_sections=6,
    )
    return bad


def _others() -> List[Dict[str, Any]]:
    from tests.test_quality_phrases import _record

    others = [_record("baseline", [(0.4, True)], 1), _record("phrases", [(0.4, True)], 1)]
    return [*others, _record("meter", [], 0)]


def _segments_key(corpus: str) -> str:
    """The reported segments-per-section key of a reported-only corpus (oversplit has no suffix)."""
    return (
        "segments_per_truth_section_reported"
        if corpus == "oversplit"
        else (f"segments_per_truth_section_{corpus}_reported")
    )


def test_every_reported_only_corpus_is_registered_and_covered_below() -> None:
    from app.quality.scoring_calc import GATED_CORPORA, REPORTED_ONLY_CORPORA

    assert set(REPORTED_ONLY_CORPORA) == {"oversplit", "steady"}
    assert not set(REPORTED_ONLY_CORPORA) & set(GATED_CORPORA)


@pytest.mark.parametrize("corpus", ["oversplit", "steady"])
def test_a_reported_only_corpus_moves_no_gated_value_and_is_reported_apart(corpus: str) -> None:
    from app.quality.scoring_calc import REPORTED_ONLY_CORPORA, aggregate

    assert corpus in REPORTED_ONLY_CORPORA
    others = _others()
    base = aggregate(others)
    out = aggregate([*others, _bad_record(corpus)])
    moved = {k for k in base if base[k] != out[k]}
    assert moved == {"tracks"}  # every gate, the ``all`` set and every other reported value
    assert out[f"boundary_f_3s_{corpus}_reported"] == 0.0
    assert out[f"beat_f_overall_{corpus}_reported"] == 0.0
    assert out[f"bpm_within_2pct_fraction_{corpus}_reported"] == 0.0
    assert out[_segments_key(corpus)] == pytest.approx(14 / 6, abs=1e-3)
    assert not [k for k in out if k.startswith("phrase_") and corpus in k]
    other = next(c for c in REPORTED_ONLY_CORPORA if c != corpus)  # the other corpus has no keys
    assert not [k for k in out if f"_{other}_" in k]
    assert f"boundary_f_3s_{other}_reported" not in out


def test_the_reported_only_corpora_do_not_mix_their_numbers() -> None:
    """The oversplit keys are the same with and without steady records beside them, and the
    steady keys hold only the steady records."""
    from app.quality.scoring_calc import aggregate

    over = _bad_record("oversplit")
    steady = _bad_record("steady")
    steady.update(boundary_f=1.0, n_segments=7, n_truth_sections=7)
    alone = aggregate([*_others(), over])
    both = aggregate([*_others(), over, steady])
    for k in alone:
        if k != "tracks":
            assert both[k] == alone[k], k
    assert both["segments_per_truth_section_steady_reported"] == pytest.approx(1.0)
    assert both["boundary_f_3s_steady_reported"] == 1.0
    assert both["segments_per_truth_section_reported"] == pytest.approx(14 / 6, abs=1e-3)


def test_a_record_of_an_unassigned_corpus_fails_loudly() -> None:
    from app.quality.scoring_calc import aggregate
    from tests.test_quality_phrases import _record

    with pytest.raises(ValueError, match="newcorpus"):
        aggregate([_record("baseline", [], 0), _record("newcorpus", [], 0)])
