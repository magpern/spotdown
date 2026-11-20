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
    assert "labels" not in doc and "alignment" not in doc
    assert "spotify" not in str(doc).lower()


def test_events_and_repetition_groups() -> None:
    core = canned_core()
    core["segments"].append({"start": 12.0, "end": 12.0, "group": 1, "label": "outro"})
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
