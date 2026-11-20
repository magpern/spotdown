"""Schema v1 files validate, their examples validate, and nothing consumer-specific leaks in."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
EXAMPLES = sorted((SCHEMA_DIR / "examples").glob("*.json"))
SCHEMAS = {
    "analysis-v1": "analysis-v1.json",
    "match-v1": "match-v1.json",
    "job-v1": "job-v1.json",
    "lookup-v1": "lookup-v1.json",
}
# Consumer-specific terms must not appear in a schema or its examples (contract-v2.md, "Generic").
FORBIDDEN = re.compile(r"owner|workout|mowl|classbeat|swedebeat|_spotify", re.IGNORECASE)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        _load(SCHEMA_DIR / SCHEMAS[name]), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_is_valid(name: str) -> None:
    Draft202012Validator.check_schema(_load(SCHEMA_DIR / SCHEMAS[name]))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_validates(path: Path) -> None:
    doc = _load(path)
    _validator(doc["schema_version"]).validate(doc)


def test_every_schema_has_an_example() -> None:
    covered = {_load(p)["schema_version"] for p in EXAMPLES}
    assert covered == set(SCHEMAS)


@pytest.mark.parametrize("path", [*SCHEMA_DIR.glob("*.json"), *EXAMPLES], ids=lambda p: p.name)
def test_nothing_consumer_specific(path: Path) -> None:
    assert not FORBIDDEN.search(path.read_text(encoding="utf-8"))


def _full() -> dict[str, Any]:
    return _load(SCHEMA_DIR / "examples" / "analysis-v1.full.json")


def test_analysis_rejects_missing_method() -> None:
    doc = _full()
    del doc["tempo"]["method"]
    with pytest.raises(ValidationError):
        _validator("analysis-v1").validate(doc)


def test_analysis_rejects_out_of_range_confidence() -> None:
    doc = _full()
    doc["events"][0]["confidence"] = 1.5
    with pytest.raises(ValidationError):
        _validator("analysis-v1").validate(doc)


def test_analysis_rejects_spotify_specific_field() -> None:
    doc = _full()
    doc["start_spotify"] = 1.0
    with pytest.raises(ValidationError):
        _validator("analysis-v1").validate(doc)


def test_analysis_labels_must_be_heuristic() -> None:
    doc = _full()
    doc["labels"][0]["method"] = "measured"
    with pytest.raises(ValidationError):
        _validator("analysis-v1").validate(doc)


def test_analysis_optional_parts_may_be_absent() -> None:
    doc = copy.deepcopy(_full())
    for key in ("labels", "alignment"):
        del doc[key]
    del doc["provenance"]["alignment"]  # no alignment at all: neither part
    for key in ("downbeats", "phrases", "meter"):
        del doc["grid"][key]
    _validator("analysis-v1").validate(doc)


def _failed_alignment(doc: Any) -> Any:
    doc = copy.deepcopy(doc)
    del doc["alignment"]
    doc["provenance"]["alignment"]["result"] = "failed"
    for key in ("chosen", "offset_s", "residual_s", "confidence"):
        doc["provenance"]["alignment"].pop(key, None)
    doc["provenance"]["run"]["failure_kind"] = "alignment_failed"
    return doc


def test_analysis_alignment_parts_agree() -> None:
    """The two alignment parts and the run's failure kind tell one story (#15 review)."""
    validator = _validator("analysis-v1")
    validator.validate(_full())
    validator.validate(_failed_alignment(_full()))
    broken = []
    doc = _failed_alignment(_full())
    doc["alignment"] = _full()["alignment"]  # a top-level alignment next to a failed record
    broken.append(doc)
    doc = _failed_alignment(_full())
    doc["provenance"]["run"]["failure_kind"] = None  # failed, but the run says nothing
    broken.append(doc)
    doc = copy.deepcopy(_full())
    doc["provenance"]["run"]["failure_kind"] = "alignment_failed"  # the record says ok
    broken.append(doc)
    doc = copy.deepcopy(_full())
    del doc["alignment"]  # ok record without the top-level alignment
    broken.append(doc)
    doc = copy.deepcopy(_full())
    del doc["provenance"]["alignment"]  # a top-level alignment without its record
    broken.append(doc)
    for doc in broken:
        with pytest.raises(ValidationError):
            validator.validate(doc)


def test_match_rate_limited_needs_retry_after() -> None:
    doc = _load(SCHEMA_DIR / "examples" / "match-v1.rate_limited.json")
    del doc["retry_after_s"]
    with pytest.raises(ValidationError):
        _validator("match-v1").validate(doc)


def test_match_allows_at_most_two_alternates() -> None:
    doc = _load(SCHEMA_DIR / "examples" / "match-v1.matched.json")
    doc["alternates"] = doc["alternates"] * 3
    with pytest.raises(ValidationError):
        _validator("match-v1").validate(doc)


def test_match_matched_needs_best() -> None:
    doc = _load(SCHEMA_DIR / "examples" / "match-v1.matched.json")
    del doc["best"]
    with pytest.raises(ValidationError):
        _validator("match-v1").validate(doc)


def _bad(name: str, doc: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validator(name).validate(doc)


def _ex(name: str) -> dict[str, Any]:
    return _load(SCHEMA_DIR / "examples" / name)


def test_every_signal_feature_needs_confidence_and_method() -> None:
    for drop in ("method", "confidence"):
        doc = _full()
        del doc["signal"][drop]
        _bad("analysis-v1", doc)
    for path in (("silence_regions", 0), ("fade_out",)):
        for drop in ("method", "confidence"):
            doc = _full()
            node: Any = doc["signal"]
            for key in path:
                node = node[key]
            del node[drop]
            _bad("analysis-v1", doc)


@pytest.mark.parametrize("group", ["half_double_candidates", "change_points"])
def test_tempo_sub_features_need_method(group: str) -> None:
    doc = _full()
    item: dict[str, Any] = {"bpm": 64.0, "confidence": 0.2}
    if group == "change_points":
        item["time_s"] = 1.0
    doc["tempo"][group] = [item]
    _bad("analysis-v1", doc)
    doc["tempo"][group][0]["method"] = "estimated"
    _validator("analysis-v1").validate(doc)


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "upload", "ref": "My Song (final).mp3"},
        {"kind": "url", "ref": "https://cdn.example/a.mp3?X-Amz-Signature=abc&token=1"},
        {"kind": "url", "ref": "https://user:pw@cdn.example/a.mp3"},
        {"kind": "url", "ref": "https://cdn.example/a.mp3#frag"},
        {"kind": "url"},
        {"kind": "youtube", "ref": "https://youtu.be/dQw4w9WgXcQ"},
        {"kind": "youtube"},
    ],
)
def test_source_ref_never_holds_names_or_signed_urls(source: dict[str, Any]) -> None:
    doc = _full()
    doc["provenance"]["source"] = source
    _bad("analysis-v1", doc)


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "upload"},
        {"kind": "url", "ref": "https://cdn.example/path/a.mp3"},
        {"kind": "url", "ref": "sha256:" + "c" * 64},
        {"kind": "youtube", "ref": "dQw4w9WgXcQ"},
    ],
)
def test_source_ref_accepts_clean_values(source: dict[str, Any]) -> None:
    doc = _full()
    doc["provenance"]["source"] = source
    _validator("analysis-v1").validate(doc)


def test_alignment_record_needs_reference_and_result() -> None:
    for drop in ("reference", "result"):
        doc = _full()
        del doc["provenance"]["alignment"][drop]
        _bad("analysis-v1", doc)
    doc = _full()
    del doc["provenance"]["alignment"]["offset_s"]
    _bad("analysis-v1", doc)


def test_failed_alignment_is_recordable() -> None:
    doc = _full()
    del doc["alignment"]
    rec = doc["provenance"]["alignment"]
    rec["result"] = "failed"
    for key in ("offset_s", "residual_s", "confidence"):
        del rec[key]
    doc["provenance"]["run"]["failure_kind"] = "alignment_failed"
    _validator("analysis-v1").validate(doc)


def test_run_failure_needs_a_kind() -> None:
    doc = _full()
    doc["provenance"]["run"]["status"] = "failed"
    _bad("analysis-v1", doc)
    doc["provenance"]["run"]["failure_kind"] = "timeout"
    _validator("analysis-v1").validate(doc)
    doc["provenance"]["run"]["failure_kind"] = "bogus"
    _bad("analysis-v1", doc)


def test_meter_may_be_heuristic() -> None:
    assert _full()["grid"]["meter"]["method"] == "heuristic"
    _validator("analysis-v1").validate(_full())


def test_created_at_is_a_date_time() -> None:
    doc = _full()
    doc["provenance"]["created_at"] = "not-a-date"
    _bad("analysis-v1", doc)


def test_match_conditionals() -> None:
    doc = _ex("match-v1.matched.json")
    doc["retry_after_s"] = 5
    _bad("match-v1", doc)
    doc = _ex("match-v1.rate_limited.json")
    doc["best"] = _ex("match-v1.matched.json")["best"]
    _bad("match-v1", doc)
    doc = _ex("match-v1.matched.json")
    doc["best"]["analysis_id"] = "an_x"
    _bad("match-v1", doc)


def test_match_cached_summary_rules() -> None:
    doc = _ex("match-v1.matched.json")
    assert "cached_analysis_id" in doc["alternates"][0]
    del doc["best"]["cached_analysis_id"]
    _bad("match-v1", doc)  # summary without id
    doc = _ex("match-v1.matched.json")
    doc["best"]["cached_summary"]["file_name"] = "x.mp3"
    _bad("match-v1", doc)  # extra field
    doc = _ex("match-v1.matched.json")
    del doc["best"]["cached_summary"]["bpm"]
    _bad("match-v1", doc)
    doc = _ex("match-v1.matched.json")
    doc["cached_analysis_id"] = "an_x"
    _bad("match-v1", doc)  # no top-level duplicate
    doc = _ex("match-v1.matched.json")
    del doc["best"]["cached_summary"]
    _validator("match-v1").validate(doc)  # id alone is fine


@pytest.mark.parametrize(
    ("band", "conf"),
    [("confident", 0.84), ("uncertain", 0.85), ("uncertain", 0.5), ("not_same", 0.6)],
)
def test_match_band_follows_confidence(band: str, conf: float) -> None:
    doc = _ex("match-v1.matched.json")
    doc["best"]["band"] = band
    doc["best"]["recording_match_confidence"] = conf
    _bad("match-v1", doc)


def test_job_conditionals() -> None:
    doc = _ex("job-v1.done.json")
    del doc["analysis_id"]
    _bad("job-v1", doc)
    doc = _ex("job-v1.failed.json")
    doc["analysis_id"] = "an_x"
    _bad("job-v1", doc)
    doc = _ex("job-v1.failed.json")
    del doc["failure_kind"]
    _bad("job-v1", doc)
    doc = _ex("job-v1.done.json")
    doc["retry_after_s"] = 3
    _bad("job-v1", doc)
    doc = _ex("job-v1.done.json")
    doc["outcome"] = "not_found"
    _bad("job-v1", doc)


@pytest.mark.parametrize("kind", ["alignment_failed", None, "other"])
def test_job_processing_failed_needs_a_processing_failure_kind(kind: Any) -> None:
    doc = _load(SCHEMA_DIR / "examples" / "job-v1.failed.json")
    doc.update(status="failed", outcome="processing_failed", failure_kind="timeout")
    _validator("job-v1").validate(doc)
    doc["failure_kind"] = kind
    with pytest.raises(ValidationError):
        _validator("job-v1").validate(doc)
