"""The external corpus format: what an index and a truth record may say."""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from app.quality import corpus_calc as cc
from app.quality.corpus_calc import CorpusError

PROV = {"title": "T", "artist": "A", "source_url": "https://example.org/t"}


def index(corpus: str = "studio", **track: Any) -> Dict[str, Any]:
    t: Dict[str, Any] = {"id": "a", "audio": "audio/a.wav", "truth": {"bpm": 120, "beats": [2, 1]}}
    t.update(track)
    return {"corpus_version": 1, "corpus": corpus, "tracks": [t]}


def test_a_valid_index_is_normalised_and_times_are_sorted() -> None:
    out = cc.validate_index(index())
    assert out["tracks"][0]["truth"] == {"bpm": 120.0, "beats": [1.0, 2.0]}


@pytest.mark.parametrize(
    "truth",
    [
        {"bpmm": 120},
        {"bpm": 0},
        {"bpm": True},
        {"bpm": float("nan")},
        {"beats": [1, -1]},
        {"beats": "1,2"},
        {"drops": [1, "x"]},
        {"four_on_the_floor": "yes"},
        {"songs": [{"start_s": 5, "end_s": 4, "bpm": 100}]},
        {"songs": [{"start_s": 1, "end_s": 4}]},
        {"duration_s": 10, "beats": [30]},
    ],
)
def test_a_malformed_truth_is_refused(truth: Dict[str, Any]) -> None:
    with pytest.raises(CorpusError):
        cc.validate_index(index(truth=truth))


@pytest.mark.parametrize("audio", ["../x.wav", "/etc/x.wav", "C:/x.wav", "a/../../x.wav", ""])
def test_an_audio_path_cannot_leave_the_corpus_directory(audio: str) -> None:
    with pytest.raises(CorpusError):
        cc.validate_index(index(audio=audio))


def test_ids_are_unique_and_keys_are_known() -> None:
    doc = index()
    doc["tracks"].append(copy.deepcopy(doc["tracks"][0]))
    with pytest.raises(CorpusError):
        cc.validate_index(doc)
    with pytest.raises(CorpusError):
        cc.validate_index(index(title="a song title"))


def test_the_version_and_the_corpus_name_are_checked() -> None:
    with pytest.raises(CorpusError):
        cc.validate_index(dict(index(), corpus_version=2))
    with pytest.raises(CorpusError):
        cc.validate_index(index(corpus="radio"))


def test_a_cc_track_needs_an_allowed_licence_and_provenance() -> None:
    ok = cc.validate_index(index("cc", licence="CC-BY-4.0", provenance=PROV))
    assert ok["tracks"][0]["licence"] == "CC-BY-4.0"
    for bad in (
        {"licence": "CC-BY-NC-4.0", "provenance": PROV},
        {"licence": "CC0-1.0"},
        {"licence": "CC0-1.0", "provenance": {"title": "T"}},
    ):
        with pytest.raises(CorpusError):
            cc.validate_index(index("cc", **bad))


def test_an_empty_list_is_an_annotation_and_a_missing_key_is_not() -> None:
    assert cc.annotated({"drops": []}, ["drops"])
    assert not cc.annotated({}, ["drops"])


def test_coverage_counts_tracks_annotated_and_sufficiency() -> None:
    rule = {"min_tracks": 2, "min_annotated": 2, "annotated_keys": ["bpm", "beats"]}
    tracks = [{"truth": {"bpm": 1, "beats": []}}, {"truth": {"bpm": 1}}]
    cov = cc.coverage(tracks, rule, [True, False])
    assert (cov["tracks"], cov["annotated"], cov["jobs_ok"], cov["sufficient"]) == (2, 1, 1, False)
    assert cc.coverage(tracks[:1] * 2, rule, [True, True])["sufficient"]


@pytest.mark.parametrize("value", ["0.1", None, True, float("nan"), float("inf")])
def test_true_offset_must_be_a_finite_number(value: Any) -> None:
    with pytest.raises(CorpusError):
        cc.validate_index(index(true_offset_s=value))
    assert cc.validate_index(index(true_offset_s=-2.5))["tracks"][0]["true_offset_s"] == -2.5
