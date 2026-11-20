"""The pure cache rules: keys, the hit decision, and the fingerprint-is-never-a-key rule."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.service import cache_policy as p
from app.service import idempotency_policy as idem

V = "1.0"


def entry(scope: Optional[str] = "a", sha="s1", version=V, fp="fp1", aid="an_1") -> p.CacheEntry:
    return p.CacheEntry(aid, scope, sha, version, fp)


@pytest.mark.parametrize(
    "entries,scope,sha,version,expect",
    [
        ([entry()], "a", "s1", V, "an_1"),  # same bytes, same scope: hit
        ([entry()], "b", "s1", V, None),  # same bytes, other scope: miss
        ([entry()], "a", "s2", V, None),  # other bytes: miss
        ([entry(fp="same")], "a", "s2", V, None),  # equal fingerprint, other sha256: miss
        ([entry(fp="same"), entry(sha="s2", fp="same", aid="an_2")], "a", "s2", V, "an_2"),
        ([entry(version="0.9")], "a", "s1", V, None),  # analyzer_version bump invalidates
        ([entry(scope=None)], "a", "s1", V, None),  # a global entry is not a content hit
        ([entry(scope="b"), entry(aid="an_2")], "a", "s1", V, "an_2"),
        ([], "a", "s1", V, None),
    ],
)
def test_content_hit(entries, scope, sha, version, expect) -> None:
    hit = p.content_hit(entries, scope=scope, sha256=sha, analyzer_version=version)
    assert (hit.analysis_id if hit else None) == expect


@pytest.mark.parametrize(
    "found,kind,scope,expect",
    [
        (entry(scope="a"), "url", "a", True),
        (entry(scope="a"), "url", "b", False),  # a link never crosses scopes
        (entry(scope=None), "youtube", "a", True),  # global source: any scope
        (entry(scope=None), "youtube", None, True),
        (entry(scope="a"), "youtube", "a", False),  # a youtube link is global, not scoped
        (entry(scope=None), "url", "a", False),
        (entry(scope="a", version="0.9"), "url", "a", False),
        (None, "url", "a", False),
    ],
)
def test_source_hit(found, kind, scope, expect) -> None:
    assert bool(p.source_hit(found, kind=kind, cache_scope=scope, analyzer_version=V)) is expect


@pytest.mark.parametrize(
    "kind,scope,expect",
    [("youtube", "a", None), ("youtube", None, None), ("url", "a", "a"), ("upload", "a", "a")],
)
def test_link_scope(kind, scope, expect) -> None:
    assert p.link_scope(kind, scope) == expect


@pytest.mark.parametrize(
    "entry_scope,caller,expect",
    [
        ("a", "a", True),
        ("a", "b", False),
        ("a", None, False),
        (None, "b", True),
        (None, None, True),
    ],
)
def test_readable_in(entry_scope, caller, expect) -> None:
    assert p.readable_in(entry_scope, caller) is expect


@pytest.mark.parametrize(
    "value,expect",
    [
        ("youtube:dQw4w9WgXcQ", ("youtube", "dQw4w9WgXcQ")),
        ("url:https://h.example.com/a", ("url", "https://h.example.com/a")),
        ("upload:x", None),
        ("youtube", None),
        ("youtube:", None),
        ("", None),
    ],
)
def test_parse_source(value, expect) -> None:
    assert p.parse_source(value) == expect


def test_alignment_key_changes_with_every_part() -> None:
    base: Dict[str, Any] = dict(
        sha256="s",
        reference_kind="k",
        reference_id="i",
        reference_duration_s=10.0,
        alignment_version="1",
    )
    keys = {p.alignment_key(**base)}
    for change in (
        {"sha256": "t"},
        {"reference_kind": "x"},
        {"reference_id": "j"},
        {"reference_duration_s": 11.0},
        {"alignment_version": "2"},
    ):
        keys.add(p.alignment_key(**{**base, **change}))
    assert len(keys) == 6
    assert p.alignment_key(**base) == p.alignment_key(**base)


@pytest.mark.parametrize(
    "value,problem",
    [
        (None, "idempotency_key_required"),
        ("", "idempotency_key_required"),
        ("k1", None),
        ("a" * 128, None),
        ("a" * 129, "invalid_request"),
        ("bad key", "invalid_request"),
        ("k\n1", "invalid_request"),
    ],
)
def test_key_problem(value, problem) -> None:
    assert idem.key_problem(value) == problem


def test_idempotency_decision_and_window() -> None:
    h = idem.request_hash("POST /x", {"a": 1, "b": 2})
    assert h == idem.request_hash("POST /x", {"b": 2, "a": 1})
    assert h != idem.request_hash("POST /y", {"a": 1, "b": 2})
    rec = idem.Recorded(h, 200, {})
    assert idem.decide(None, h) == "new"
    assert idem.decide(rec, h) == "replay"
    assert idem.decide(rec, "other") == "conflict"
    assert not idem.expired(0, idem.WINDOW_S - 1) and idem.expired(0, idem.WINDOW_S)
