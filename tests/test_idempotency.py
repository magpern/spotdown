"""``Idempotency-Key`` on PUT /v2/uploads and POST /v2/analyses: replay, 409, window, scope."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .cache_helpers import Stack
from .service_helpers import AUTH, SCOPE, audio_files, client_for


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def test_the_header_is_required_on_both_endpoints(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    plain = client_for(stack.settings, stack.dispatch, keyed=False)
    put = plain.put(
        f"/v2/uploads?cache_scope={SCOPE}",
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=stack.audio("a.wav"),
    )
    post = plain.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": "up_1"}
    )
    for res in (put, post):
        assert res.status_code == 422 and res.json()["error"]["code"] == "idempotency_key_required"
    assert audio_files(stack.settings) == [] and stack.dispatch.job_ids == []


@pytest.mark.parametrize("key", ["bad key", "k" * 129, "a/b"])
def test_a_malformed_key_is_refused(stack: Stack, key: str) -> None:
    res = stack.upload(stack.audio("a.wav"), key=key)
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    assert audio_files(stack.settings) == []


def test_an_upload_replay_returns_the_first_response_and_keeps_one_file(stack: Stack) -> None:
    data = stack.audio("a.wav")
    first = stack.upload(data, key="up-1")
    again = stack.upload(data, key="up-1")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert [p.name for p in stack.settings.uploads_dir.iterdir()] == [first.json()["upload_id"]]


def test_an_upload_key_reused_for_other_bytes_is_409(stack: Stack) -> None:
    first = stack.upload(stack.audio("a.wav", 2.0), key="up-1")
    other = stack.upload(stack.audio("b.wav", 3.0), key="up-1")
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    assert [p.name for p in stack.settings.uploads_dir.iterdir()] == [first.json()["upload_id"]]


def test_an_analysis_replay_returns_the_first_response_and_starts_one_job(stack: Stack) -> None:
    upload_id = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    first = stack.analyse(upload_id, key="an-1")
    again = stack.analyse(upload_id, key="an-1")  # the upload is consumed, the key still replays
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json()
    assert len(stack.dispatch.job_ids) == 1


def test_an_analysis_cache_hit_replays_as_a_hit(stack: Stack) -> None:
    data = stack.audio("a.wav")
    stack.analysis_id(stack.analyse(stack.upload(data).json()["upload_id"]))
    up2 = stack.upload(data).json()["upload_id"]
    first = stack.analyse(up2, key="an-2")
    again = stack.analyse(up2, key="an-2")
    assert first.status_code == again.status_code == 200 and again.json() == first.json()


def test_an_analysis_key_reused_for_another_request_is_409(stack: Stack) -> None:
    up1 = stack.upload(stack.audio("a.wav", 2.0)).json()["upload_id"]
    up2 = stack.upload(stack.audio("b.wav", 3.0)).json()["upload_id"]
    assert stack.analyse(up1, key="an-1").status_code == 202
    for body in (
        {"cache_scope": SCOPE, "upload_id": up2},
        {"cache_scope": SCOPE, "upload_id": up1, "client_ref": "x"},
    ):
        res = stack.post(body, key="an-1")
        assert res.status_code == 409 and res.json()["error"]["code"] == "idempotency_conflict"
    assert len(stack.dispatch.job_ids) == 1


def test_a_key_is_remembered_per_scope(stack: Stack) -> None:
    data = stack.audio("a.wav")
    assert stack.upload(data, "scope_a", key="same").status_code == 201
    other = stack.upload(stack.audio("b.wav", 3.0), "scope_b", key="same")
    assert other.status_code == 201  # no conflict across scopes


def test_the_same_key_on_the_other_endpoint_is_a_conflict(stack: Stack) -> None:
    up = stack.upload(stack.audio("a.wav"), key="shared")
    assert up.status_code == 201
    res = stack.analyse(up.json()["upload_id"], key="shared")
    assert res.status_code == 409


def test_a_key_expires_after_24_hours(stack: Stack) -> None:
    first = stack.upload(stack.audio("a.wav", 2.0), key="old")
    with stack.store._conn() as conn:
        conn.execute("UPDATE idempotency SET created_at = ?", (time.time() - 24 * 3600 - 1,))
    fresh = stack.upload(stack.audio("b.wav", 3.0), key="old")  # a different body is fine now
    assert fresh.status_code == 201 and fresh.json()["upload_id"] != first.json()["upload_id"]


def test_a_refused_request_leaves_no_record_and_the_key_stays_free(stack: Stack) -> None:
    res = stack.post({"cache_scope": SCOPE, "upload_id": "up_missing"}, key="k1")
    assert res.status_code == 404
    up = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    assert stack.analyse(up, key="k1").status_code == 202


def test_a_queue_outage_frees_the_key(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    up = stack.upload(stack.audio("a.wav")).json()["upload_id"]
    stack.dispatch.then = lambda _id: (_ for _ in ()).throw(ConnectionError("down"))
    assert stack.analyse(up, key="retry").status_code == 503
    stack.dispatch.then = stack._run
    assert stack.analyse(up, key="retry").status_code == 202


def test_expired_records_are_purged_on_the_next_insert(stack: Stack) -> None:
    stack.upload(stack.audio("a.wav", 2.0), key="old-1")
    stack.upload(stack.audio("b.wav", 3.0), "scope_b", key="old-2")
    with stack.store._conn() as conn:
        conn.execute("UPDATE idempotency SET created_at = ?", (time.time() - 24 * 3600 - 1,))
    stack.upload(stack.audio("c.wav", 4.0), key="new")
    with stack.store._conn() as conn:
        keys = [r["idem_key"] for r in conn.execute("SELECT idem_key FROM idempotency")]
    assert keys == ["new"]
