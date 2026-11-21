"""The explicit-content signal (spotdown#36): every evidence combination gives the documented value
and confidence, nothing that is not evidence is counted, and the schema accepts exactly the
consistent ``content.explicit`` objects."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from jsonschema import ValidationError

from app.service import explicit_calc as calc

from .service_helpers import assert_valid, validator

FULL = Path(__file__).resolve().parent.parent / "schema" / "examples" / "analysis-v1.full.json"


def _full() -> Dict[str, Any]:
    return json.loads(FULL.read_text(encoding="utf-8"))


YES_CALLER = {"source": "caller", "value": True}
NO_CALLER = {"source": "caller", "value": False}
YES_MATCH = {"source": "caller_match", "value": True}
NO_MATCH = {"source": "caller_match", "value": False}
AGE18 = {"source": "youtube_age_limit", "value": 18}


@pytest.mark.parametrize(
    "kwargs,value,confidence,evidence",
    [
        ({}, "unknown", 0.0, []),
        ({"caller": True}, "yes", 0.9, [YES_CALLER]),
        ({"caller": False}, "no", 0.8, [NO_CALLER]),
        ({"caller_match": True}, "yes", 0.9, [YES_MATCH]),
        ({"caller_match": False}, "no", 0.8, [NO_MATCH]),
        ({"age_limit": 18}, "yes", 0.6, [AGE18]),
        ({"caller": True, "age_limit": 21}, "yes", 0.9, [YES_CALLER, {**AGE18, "value": 21}]),
        ({"caller": False, "age_limit": 18}, "yes", 0.5, [NO_CALLER, AGE18]),  # conflict
        ({"caller": True, "caller_match": False}, "yes", 0.5, [YES_CALLER, NO_MATCH]),
        ({"caller": False, "caller_match": True}, "yes", 0.5, [NO_CALLER, YES_MATCH]),
        ({"caller": False, "caller_match": False}, "no", 0.8, [NO_CALLER, NO_MATCH]),
        (
            {"caller": True, "caller_match": True, "age_limit": 18},
            "yes",
            0.9,
            [YES_CALLER, YES_MATCH, AGE18],
        ),  # fmt: skip
    ],
)
def test_each_evidence_combination(
    kwargs: Dict[str, Any], value: str, confidence: float, evidence: List[Dict[str, Any]]
) -> None:
    found = calc.explicit(calc.gather(**kwargs))
    assert found == {
        "value": value,
        "confidence": confidence,
        "method": "metadata",
        "evidence": evidence,
    }
    doc = _full()
    doc["content"] = {"explicit": found}
    assert_valid("analysis-v1", doc)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"caller": "true"},
        {"caller": 1},
        {"caller_match": 0},
        {"age_limit": 17},
        {"age_limit": 0},
        {"age_limit": True},  # a bool is not an age
        {"age_limit": 18.0},
        {"age_limit": "18"},
    ],
)
def test_anything_else_is_no_evidence_and_stays_unknown(kwargs: Dict[str, Any]) -> None:
    assert calc.gather(**kwargs) == []
    assert calc.explicit([])["value"] == "unknown"


def test_the_content_group_and_its_evidence_are_copies() -> None:
    evidence = calc.gather(caller=True)
    group = calc.content(evidence)
    group["explicit"]["evidence"][0]["value"] = False
    assert evidence == [YES_CALLER]


def _with(explicit: Dict[str, Any]) -> Dict[str, Any]:
    doc = _full()
    doc["content"] = {"explicit": explicit}
    return doc


BASE = {"value": "unknown", "confidence": 0.0, "method": "metadata", "evidence": []}


@pytest.mark.parametrize(
    "change",
    [
        {"value": "no"},  # no without evidence
        {"value": "no", "confidence": 0.8, "evidence": [AGE18]},  # an age limit is never no
        {"value": "no", "confidence": 0.5, "evidence": [NO_CALLER, YES_MATCH]},
        {"value": "yes", "confidence": 0.8, "evidence": [NO_CALLER]},  # yes without yes evidence
        {"value": "yes", "confidence": 0.8, "evidence": []},
        {"evidence": [YES_CALLER]},  # unknown with evidence
        {"confidence": 0.3},  # unknown with a confidence
        {"method": "heuristic"},  # phase 1 is metadata only
        {"value": "maybe"},
        {"evidence": [{"source": "youtube_age_limit", "value": 17}]},
        {"evidence": [{"source": "caller", "value": 1}]},
        {"evidence": [{"source": "lyrics", "value": True}]},
        {"evidence": [{**YES_CALLER, "extra": 1}]},
    ],
)
def test_the_schema_refuses_an_inconsistent_signal(change: Dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validator("analysis-v1").validate(_with({**BASE, **change}))


def test_the_schema_refuses_a_content_group_without_explicit_or_with_extras() -> None:
    doc = _full()
    for bad in ({}, {"explicit": BASE, "violence": BASE}):
        doc["content"] = copy.deepcopy(bad)
        with pytest.raises(ValidationError):
            validator("analysis-v1").validate(doc)


def test_the_content_group_is_optional() -> None:
    doc = _full()
    del doc["content"]
    assert_valid("analysis-v1", doc)
