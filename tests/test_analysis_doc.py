"""The analysis-v1 formatter (pure): schema-valid, file clock, no assumed meter."""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from app.service import analysis_doc

from .service_helpers import assert_valid, canned_core


def build(core: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    return analysis_doc.build(
        core=core,
        probe={"codec": "mp3", "sample_rate_hz": 44100, "channels": 2, "bitrate_bps": 320000},
        components={"librosa": "0.10.2", "numpy": "2.0"},
        sha256="c" * 64,
        source=source,
        job_id="job_1",
        analysis_id="an_1",
        timings_s={"fetch": 0.5, "decode": 0.1, "analyse": -0.0},
        created_at="2026-11-20T10:00:00Z",
    )


@pytest.mark.parametrize(
    "source",
    [{"kind": "upload"}, {"kind": "url", "ref": "https://media.example.com/a/b.mp3"}],
)
def test_the_document_is_schema_valid(source) -> None:
    doc = build(canned_core(), source)
    assert_valid("analysis-v1", doc)
    assert doc["provenance"]["source"] == source
    assert doc["provenance"]["analyzer"]["analyzer_version"] == analysis_doc.ANALYZER_VERSION_V2
    for absent in ("meter", "downbeats", "phrases"):
        assert absent not in doc["grid"]
    assert "alignment" not in doc
    assert "spotify" not in str(doc).lower()


def test_events_and_repetition_groups() -> None:
    core = canned_core()
    core["segments"].append(
        {"start": 12.0, "end": 12.0, "group": 1, "label": "outro", "energy_struct": 0.2}
    )
    doc = build(core, {"kind": "upload"})
    kinds = [e["kind"] for e in doc["events"]]
    assert kinds == ["build_up", "drop"]
    assert doc["structure"]["repetition_groups"] == [
        {"segment_ids": ["s1", "s3"], "confidence": 0.5, "method": "estimated"}
    ]


def test_no_tempo_is_an_analysis_error() -> None:
    core = canned_core()
    core["bpm"] = 0.0
    with pytest.raises(ValueError):
        build(core, {"kind": "upload"})


@pytest.mark.parametrize(
    "beats,expected",
    [
        ([], 0.0),
        ([0.0, 0.5, 1.0], 0.0),
        ([0.0, 0.5, 1.0, 1.5, 2.0], 1.0),
    ],
)
def test_beat_stability(beats, expected) -> None:
    assert analysis_doc.beat_stability(beats) == pytest.approx(expected, abs=0.05)


def test_uneven_beats_are_less_stable() -> None:
    assert analysis_doc.beat_stability([0.0, 0.5, 1.2, 1.5, 2.3]) < 0.5


def test_the_fingerprint_is_stable_and_coarse() -> None:
    a = analysis_doc.fingerprint([0.1, 0.5, 0.9])
    assert a == analysis_doc.fingerprint([0.11, 0.51, 0.91])
    assert a != analysis_doc.fingerprint([0.9, 0.5, 0.1])
    assert a.startswith("energy16-v1:")


def test_the_builder_does_not_change_its_input() -> None:
    core = canned_core()
    before = copy.deepcopy(core)
    build(core, {"kind": "upload"})
    assert core == before


def test_labels_are_heuristic_schema_valid_and_name_existing_segments() -> None:
    doc = build(canned_core(), {"kind": "upload"})
    assert_valid("analysis-v1", doc)
    ids = {s["id"] for s in doc["structure"]["segments"]}
    assert doc["labels"] and {x["segment_id"] for x in doc["labels"]} <= ids
    assert {x["method"] for x in doc["labels"]} == {"heuristic"}
    assert [x["label"] for x in doc["labels"]] == [
        "intro",
        "chorus",
    ]  # the second segment starts at the drop


def test_labels_are_absent_when_there_is_no_structure_to_label() -> None:
    core = canned_core()
    core["segments"] = core["segments"][:1]
    assert "labels" not in build(core, {"kind": "upload"})


def _phrase_core() -> Dict[str, Any]:
    core = canned_core(40)  # 120 BPM: a bar is 2 s
    core["content_end_s"] = 39.5
    core["segments"] = [
        {"start": 0.0, "end": 16.4, "group": 1, "label": "intro", "energy_struct": 0.2},
        {"start": 16.4, "end": 32.4, "group": 2, "label": "verse", "energy_struct": 0.6},
        {"start": 32.4, "end": 40.0, "group": 3, "label": "outro", "energy_struct": 0.3},
    ]
    return core


def test_phrases_are_heuristic_low_confidence_and_schema_valid() -> None:
    doc = build(_phrase_core(), {"kind": "upload"})
    assert_valid("analysis-v1", doc)
    found = doc["grid"]["phrases"]
    assert [(p["start_s"], p["end_s"], p["bars"]) for p in found] == [
        (0.4, 16.4, 8),
        (16.4, 32.4, 8),
    ]
    assert {p["method"] for p in found} == {"heuristic"}
    assert max(p["confidence"] for p in found) <= 0.4
    assert "meter" not in doc["grid"] and "downbeats" not in doc["grid"]


def test_phrases_are_absent_when_no_section_is_a_whole_phrase() -> None:
    core = _phrase_core()
    # boundaries at 0.4, 13.0 and 27.0 s: 6.3, 7 and 13.3 bars apart, none a phrase
    core["segments"][0]["end"] = core["segments"][1]["start"] = 13.0
    core["segments"][1]["end"] = core["segments"][2]["start"] = 27.0
    assert "phrases" not in build(core, {"kind": "upload"})["grid"]
