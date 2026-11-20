"""The alignment layer's documents (spotdown#11): the aligned analysis adds evidence only."""

from __future__ import annotations

import copy
from typing import Any, Dict

from app.service import alignment_doc as ad
from app.service import analysis_doc

from .service_helpers import assert_valid, fake_analyser_result

SHA = "a" * 64
REF = {"kind": "spotify", "id": "4uLU6hMCjMI75M1A2tKUQC", "duration_s": 12.0}


def base(source: Any = None) -> Dict[str, Any]:
    r = fake_analyser_result()  # 12 s file, content 0.4 to 11.5 s
    return analysis_doc.build(
        core=r["core"],
        probe=r["probe"],
        components=r["components"],
        sha256=SHA,
        source=source or {"kind": "upload"},
        job_id="job_1",
        analysis_id="an_base",
        timings_s={},
        created_at="2026-11-20T00:00:00Z",
    )


def core_part(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The document without what alignment may add or set."""
    doc = copy.deepcopy(doc)
    for key in ("analysis_id", "alignment"):
        doc.pop(key, None)
    doc["provenance"].pop("alignment", None)
    doc["provenance"]["run"]["failure_kind"] = None
    return doc


def test_an_ok_alignment_adds_the_object_and_the_record_only() -> None:
    b = base()
    row = ad.build(b, REF, "an_aligned")
    doc = ad.compose(b, row)
    assert_valid("analysis-v1", doc)
    assert doc["analysis_id"] == "an_aligned" and row.base_analysis_id == "an_base"
    assert doc["alignment"] == {
        "reference": REF,
        "offset_s": 0.0,
        "residual_s": 0.0,
        "confidence": 0.95,  # the trimmed mapping misses by only 0.9 s
        "method": "estimated",
    }
    rec = doc["provenance"]["alignment"]
    assert rec["result"] == "ok" and rec["chosen"] == "untrimmed"
    assert rec["alignment_version"] == "1" and rec["reference"] == REF
    assert rec["hypotheses"] == [
        {"name": "untrimmed", "offset_s": 0.0, "residual_s": 0.0},
        {"name": "trimmed", "offset_s": -0.4, "residual_s": 0.9},
    ]
    assert doc["provenance"]["run"]["failure_kind"] is None
    assert core_part(doc) == core_part(b)
    assert b == base()  # the base document is not modified


def test_the_trimmed_hypothesis_is_chosen_for_trimmed_content() -> None:
    doc = ad.compose(base(), ad.build(base(), {**REF, "duration_s": 11.1}, "an_x"))
    assert doc["alignment"]["offset_s"] == -0.4 and doc["alignment"]["residual_s"] == 0.0
    assert doc["provenance"]["alignment"]["chosen"] == "trimmed"
    assert_valid("analysis-v1", doc)


def test_a_failed_alignment_keeps_the_file_clock_analysis() -> None:
    row = ad.build(base(), {**REF, "duration_s": 30.0}, "an_x")
    assert row.failed and row.outcome == "alignment_failed" and row.alignment is None
    doc = ad.compose(base(), row)
    assert_valid("analysis-v1", doc)
    assert "alignment" not in doc
    rec = doc["provenance"]["alignment"]
    assert rec["result"] == "failed" and len(rec["hypotheses"]) == 2
    assert not {"chosen", "offset_s", "residual_s", "confidence"} & set(rec)
    assert doc["provenance"]["run"] == {
        **base()["provenance"]["run"],
        "failure_kind": ad.ALIGNMENT_FAILED,
    }
    assert core_part(doc) == core_part(base())


def test_the_recording_match_confidence_is_carried_from_the_match() -> None:
    match = {
        "matcher_version": "youtube-1",
        "normalised_query": "q",
        "candidates": [
            {"ref": "otherid0001", "score": 0.5, "duration_delta_s": 3.0},
            {"ref": "dQw4w9WgXcQ", "score": 0.91, "duration_delta_s": 0.2},
        ],
        "chosen": "dQw4w9WgXcQ",
    }
    b = base({"kind": "youtube", "ref": "dQw4w9WgXcQ", "match": match})
    assert ad.build(b, REF, "an_x").alignment["recording_match_confidence"] == 0.91  # type: ignore[index]
    other = base({"kind": "youtube", "ref": "otherid0001", "match": match})
    assert "recording_match_confidence" not in (ad.build(other, REF, "an_y").alignment or {})
    assert "recording_match_confidence" not in (ad.build(base(), REF, "an_z").alignment or {})


def test_the_cache_key_is_audio_reference_and_version() -> None:
    keys = {ad.cache_key(base(), REF)}
    for change in ({"kind": "other"}, {"id": "x"}, {"duration_s": 12.5}):
        keys.add(ad.cache_key(base(), {**REF, **change}))
    other_audio = base()
    other_audio["provenance"]["audio"]["sha256"] = "b" * 64
    keys.add(ad.cache_key(other_audio, REF))
    assert len(keys) == 5
    assert ad.cache_key(base(), REF) == ad.cache_key(base(), dict(REF))


def test_a_row_round_trips_through_its_stored_form() -> None:
    for duration in (12.0, 30.0):
        row = ad.build(base(), {**REF, "duration_s": duration}, "an_x")
        assert ad.AlignedRow.from_doc(row.as_doc()) == row
