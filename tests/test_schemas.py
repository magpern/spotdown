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
SCHEMAS = {"analysis-v1": "analysis-v1.json", "match-v1": "match-v1.json"}
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
    for key in ("downbeats", "phrases", "meter"):
        del doc["grid"][key]
    _validator("analysis-v1").validate(doc)


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
