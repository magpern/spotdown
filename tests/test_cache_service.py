"""The caches end to end: hits, misses, scopes, fingerprint, version bump, lookups, GET analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.service import analysis_doc

from .cache_helpers import URL, Stack, sha
from .service_helpers import AUTH, SCOPE, assert_valid, audio_files


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def test_same_bytes_in_the_same_scope_is_a_hit_without_reanalysis(stack: Stack) -> None:
    data = stack.audio("a.wav")
    first = stack.analyse_bytes(data)
    assert first.status_code == 202
    analysis_id = stack.analysis_id(first)
    assert stack.analysed == [sha(data)]

    # the sha256 lookup finds it before any upload
    found = stack.lookup(sha256=sha(data))
    assert found.status_code == 200
    assert found.json() == {"schema_version": "lookup-v1", "analysis_id": analysis_id}
    assert_valid("lookup-v1", found.json())

    again = stack.analyse_bytes(data)
    assert again.status_code == 200
    body = again.json()
    assert body["status"] == "done" and body["analysis_id"] == analysis_id
    assert stack.analysed == [sha(data)]  # no second analysis
    assert len(stack.dispatch.job_ids) == 1  # and nothing queued
    assert audio_files(stack.settings) == []  # the hit's upload is gone
    job = stack.client.get(f"/v2/jobs/{body['job_id']}?cache_scope={SCOPE}", headers=AUTH).json()
    assert_valid("job-v1", job)
    assert job["status"] == "done" and job["analysis_id"] == analysis_id


def test_the_same_bytes_in_another_scope_are_a_miss(stack: Stack) -> None:
    data = stack.audio("a.wav")
    first_id = stack.analysis_id(stack.analyse_bytes(data, "scope_a"), "scope_a")
    assert stack.lookup("scope_b", sha256=sha(data)).status_code == 404
    other = stack.analyse_bytes(data, "scope_b")
    assert other.status_code == 202
    other_id = stack.analysis_id(other, "scope_b")
    assert other_id != first_id
    assert stack.analysed == [sha(data), sha(data)]
    assert stack.lookup("scope_b", sha256=sha(data)).json()["analysis_id"] == other_id
    assert stack.lookup("scope_a", sha256=sha(data)).json()["analysis_id"] == first_id


def test_an_equal_fingerprint_with_a_different_sha256_is_a_miss_and_is_recorded(
    stack: Stack,
) -> None:
    a, b = stack.audio("a.wav", 2.0), stack.audio("b.wav", 3.0)
    assert sha(a) != sha(b)
    stack.fingerprint_energy = [0.3, 0.6]  # both files get the same decoded-audio fingerprint
    id_a = stack.analysis_id(stack.analyse_bytes(a))
    second = stack.analyse_bytes(b)
    assert second.status_code == 202  # a miss: it is analysed
    id_b = stack.analysis_id(second)
    assert id_a != id_b and stack.analysed == [sha(a), sha(b)]
    doc_a, doc_b = (stack.get_analysis(i).json() for i in (id_a, id_b))
    assert (
        doc_a["provenance"]["audio"]["fingerprint"] == doc_b["provenance"]["audio"]["fingerprint"]
    )
    assert doc_a["provenance"]["audio"]["sha256"] == sha(a)
    assert doc_b["provenance"]["audio"]["sha256"] == sha(b)
    (row,) = stack.store.entries_by_sha(SCOPE, sha(b))
    assert row.fingerprint == doc_b["provenance"]["audio"]["fingerprint"]
    assert stack.lookup(sha256=sha(b)).json()["analysis_id"] == id_b


def test_raising_analyzer_version_invalidates_the_cache(stack: Stack, monkeypatch) -> None:
    data = stack.audio("a.wav")
    old_id = stack.analysis_id(stack.analyse_bytes(data))
    assert stack.analyse_bytes(data).status_code == 200
    monkeypatch.setattr(analysis_doc, "ANALYZER_VERSION_V2", "999.0")
    assert stack.lookup(sha256=sha(data)).status_code == 404
    res = stack.analyse_bytes(data)
    assert res.status_code == 202  # re-analysed
    new_id = stack.analysis_id(res)
    assert new_id != old_id and len(stack.analysed) == 2
    new_doc = stack.get_analysis(new_id).json()
    assert new_doc["provenance"]["analyzer"]["analyzer_version"] == "999.0"
    assert stack.lookup(sha256=sha(data)).json()["analysis_id"] == new_id
    # the old document is still readable by id; it is just not served by lookups
    assert stack.get_analysis(old_id).status_code == 200


def test_the_same_url_in_the_same_scope_is_a_hit_through_the_source_link(stack: Stack) -> None:
    stack.fetch_bytes = stack.audio("u.wav")
    first = stack.post({"cache_scope": SCOPE, "url": URL + "?sig=secret"})
    assert first.status_code == 202
    analysis_id = stack.analysis_id(first)
    found = stack.lookup(source=f"url:{URL}?sig=secret#frag")
    assert found.status_code == 200 and found.json()["analysis_id"] == analysis_id
    again = stack.post({"cache_scope": SCOPE, "url": URL + "?sig=secret"})
    assert again.status_code == 200 and again.json()["analysis_id"] == analysis_id
    assert len(stack.analysed) == 1 and len(stack.dispatch.job_ids) == 1
    assert stack.lookup("scope_b", source=f"url:{URL}?sig=secret").status_code == 404
    assert stack.post({"cache_scope": "scope_b", "url": URL}).status_code == 202


def test_urls_that_differ_only_in_their_query_are_different_sources(stack: Stack) -> None:
    """``?id=1`` and ``?id=2`` may be different audio: no source hit across them. The same
    bytes behind another query are still reused through their sha256, without a second run."""
    stack.fetch_bytes = stack.audio("u.wav")
    analysis_id = stack.analysis_id(stack.post({"cache_scope": SCOPE, "url": URL + "?id=1"}))
    for other in (URL, URL + "?id=2"):
        assert stack.lookup(source=f"url:{other}").status_code == 404
    second = stack.post({"cache_scope": SCOPE, "url": URL + "?id=2"})
    assert second.status_code == 202  # not answered from the first URL's link
    assert stack.analysis_id(second) == analysis_id  # equal bytes: reused, not re-analysed
    assert len(stack.analysed) == 1
    stack.fetch_bytes = stack.audio("v.wav", seconds=3.0)  # other audio behind ?id=3
    third = stack.analysis_id(stack.post({"cache_scope": SCOPE, "url": URL + "?id=3"}))
    assert third != analysis_id and len(stack.analysed) == 2


def test_a_fetched_file_already_analysed_in_the_scope_is_reused(stack: Stack) -> None:
    data = stack.audio("a.wav")
    upload_analysis = stack.analysis_id(stack.analyse_bytes(data))
    stack.fetch_bytes = data  # the URL serves the same bytes
    res = stack.post({"cache_scope": SCOPE, "url": URL})
    assert res.status_code == 202  # the URL itself is new
    assert stack.analysis_id(res) == upload_analysis
    assert stack.analysed == [sha(data)]  # no second analysis
    assert stack.lookup(source=f"url:{URL}").json()["analysis_id"] == upload_analysis


LOOKUPS = [
    ("sha", {"sha256": "{sha}"}),
    ("url", {"source": f"url:{URL}"}),
]


@pytest.mark.parametrize("name,query", LOOKUPS)
@pytest.mark.parametrize("other", ["scope_b", "other-scope", "S"])
def test_lookups_never_cross_scopes(stack: Stack, name, query, other) -> None:
    data = stack.audio("a.wav")
    stack.fetch_bytes = data
    stack.analysis_id(stack.analyse_bytes(data))
    stack.analysis_id(stack.post({"cache_scope": SCOPE, "url": URL}))
    q = {k: v.format(sha=sha(data)) for k, v in query.items()}
    assert stack.lookup(SCOPE, **q).status_code == 200
    miss = stack.lookup(other, **q)
    assert miss.status_code == 404 and miss.json()["error"]["code"] == "not_found"
    assert set(miss.json()) == {"error"}


@pytest.mark.parametrize(
    "scope,query,status,code",
    [
        (None, {"sha256": "a" * 64}, 422, "scope_required"),
        ("", {"sha256": "a" * 64}, 422, "scope_required"),
        (SCOPE, {"sha256": "A" * 64}, 422, "invalid_request"),
        (SCOPE, {"sha256": "abc"}, 422, "invalid_request"),
        ("bad scope", {"sha256": "a" * 64}, 422, "invalid_request"),
        (SCOPE, {}, 422, "invalid_request"),
        (SCOPE, {"sha256": "a" * 64, "source": "youtube:dQw4w9WgXcQ"}, 422, "invalid_request"),
        (None, {"source": "url:https://media.example.com/a"}, 422, "scope_required"),
        (SCOPE, {"source": "upload:x"}, 422, "invalid_request"),
        (SCOPE, {"source": "youtube:short"}, 422, "invalid_request"),
        (None, {"source": "youtube:dQw4w9WgXcQ"}, 404, "not_found"),  # global: scope optional
    ],
)
def test_lookup_requests(stack: Stack, scope, query, status, code) -> None:
    res = stack.lookup(scope, **query)
    assert res.status_code == status and res.json()["error"]["code"] == code


def test_a_global_youtube_entry_is_shared_by_every_scope(stack: Stack) -> None:
    data = stack.audio("a.wav")
    analysis_id = stack.analysis_id(stack.analyse_bytes(data, "scope_a"), "scope_a")
    doc = stack.store.get_analysis(analysis_id)
    assert doc is not None
    doc["provenance"]["source"] = {"kind": "youtube", "ref": "dQw4w9WgXcQ"}
    with stack.store._conn() as conn:
        from app.service.cache_store import insert_analysis

        insert_analysis(conn, "an_global", None, doc, 1.0)
    for scope in ("scope_a", "scope_b", None):
        res = stack.lookup(scope, source="youtube:dQw4w9WgXcQ")
        assert res.status_code == 200 and res.json()["analysis_id"] == "an_global"
        assert stack.get_analysis("an_global", scope).status_code == 200


def test_get_analysis_is_scope_checked(stack: Stack) -> None:
    analysis_id = stack.analysis_id(stack.analyse_bytes(stack.audio("a.wav")))
    ok = stack.get_analysis(analysis_id)
    assert ok.status_code == 200
    assert_valid("analysis-v1", ok.json())
    assert ok.json()["analysis_id"] == analysis_id
    for scope in ("scope_b", None, ""):
        res = stack.get_analysis(analysis_id, scope)
        assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    for bad in ("an_missing", "../../x", "a" * 80):
        assert stack.get_analysis(bad).status_code == 404
    assert stack.get_analysis(analysis_id, "bad scope").status_code == 422
    assert stack.client.get(f"/v2/analyses/{analysis_id}").status_code == 401


def test_every_stored_analysis_validates_and_has_full_provenance(stack: Stack) -> None:
    secret_url = URL + "?X-Signature=s3cr3t-token"
    stack.fetch_bytes = stack.audio("u.wav", 3.0)
    ids = [
        stack.analysis_id(stack.analyse_bytes(stack.audio("a.wav"))),
        stack.analysis_id(stack.post({"cache_scope": SCOPE, "url": secret_url})),
    ]
    with stack.store._conn() as conn:
        stored = [json.loads(r["doc"]) for r in conn.execute("SELECT doc FROM analyses")]
    assert len(stored) == 2
    for doc in stored:
        assert_valid("analysis-v1", doc)
        prov = doc["provenance"]
        assert set(prov) >= {"analyzer", "audio", "source", "run", "created_at"}
        assert prov["analyzer"]["analyzer_version"] == analysis_doc.ANALYZER_VERSION_V2
        assert prov["analyzer"]["config_hash"] and prov["analyzer"]["component_versions"]
        assert len(prov["audio"]["sha256"]) == 64 and prov["audio"]["fingerprint"]
        assert {"codec", "sample_rate_hz", "channels", "file_duration_s"} <= set(prov["audio"])
        assert prov["run"]["status"] == "done" and prov["run"]["failure_kind"] is None
        assert prov["run"]["job_id"].startswith("job_") and prov["run"]["timings_s"]
        text = json.dumps(doc)
        for forbidden in ("s3cr3t", "X-Signature", "a.wav", "u.wav", "filename", "token"):
            assert forbidden not in text
    kinds = sorted(d["provenance"]["source"]["kind"] for d in stored)
    assert kinds == ["upload", "url"]
    assert all(
        "ref" not in d["provenance"]["source"]
        for d in stored
        if d["provenance"]["source"]["kind"] == "upload"
    )
    assert {i for i in ids} == {d["analysis_id"] for d in stored}
