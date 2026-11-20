"""PUT /v2/uploads: streamed, capped, mp3/mp4/wav only, scoped, with a short TTL."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import anyio
import pytest

from app.main import create_app
from app.service.store import Store
from app.service.uploads import purge_expired_uploads

from .service_helpers import (
    AUTH,
    SCOPE,
    RecordingDispatch,
    client_for,
    service_settings,
    wav_bytes,
)

OCTET = {**AUTH, "Content-Type": "application/octet-stream"}


@pytest.fixture()
def settings(tmp_path: Path):
    return service_settings(tmp_path, max_upload_bytes=200_000)


def _files(settings) -> list:
    return sorted(p.name for p in settings.uploads_dir.iterdir())


def test_an_upload_is_stored_without_a_name(settings, tmp_path: Path) -> None:
    data = wav_bytes(tmp_path, 2.0)
    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}&filename=evil.wav", headers=OCTET, content=data
    )
    assert res.status_code == 201
    body = res.json()
    assert set(body) == {"upload_id", "sha256", "bytes"}
    assert body["sha256"] == hashlib.sha256(data).hexdigest() and body["bytes"] == len(data)
    assert _files(settings) == [body["upload_id"]]
    assert (settings.uploads_dir / body["upload_id"]).read_bytes() == data
    row = Store(settings.service_db_path).get_upload(body["upload_id"])
    assert row is not None and row["cache_scope"] == SCOPE and row["media"] == "wav"
    assert "evil" not in str(row)


@pytest.mark.parametrize(
    "head", [b"ID3\x04\x00\x00\x00\x00\x00\x00", b"\xff\xfb\x90\x64", b"\x00\x00\x00\x20ftypM4A "]
)
def test_mp3_and_mp4_are_accepted(settings, head: bytes) -> None:
    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=head + b"\x00" * 100
    )
    assert res.status_code == 201


@pytest.mark.parametrize("data", [b"OggS" + b"\x00" * 100, b"<html>" * 10, b"", b"RIFF"])
def test_other_content_is_refused_and_not_kept(settings, data: bytes) -> None:
    res = client_for(settings).put(f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=data)
    assert res.status_code == 415
    assert res.json()["error"]["code"] == "unsupported_media_type"
    assert _files(settings) == []


@pytest.mark.parametrize("ctype", ["audio/wav", "multipart/form-data; boundary=x", ""])
def test_only_octet_stream_is_accepted(settings, tmp_path: Path, ctype: str) -> None:
    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}",
        headers={**AUTH, "Content-Type": ctype},
        content=wav_bytes(tmp_path, 1.0),
    )
    assert res.status_code == 415
    assert _files(settings) == []


def test_a_declared_length_over_the_cap_is_refused_unread(settings) -> None:
    consumed = []

    def body():
        consumed.append(1)
        yield b"RIFF\x00\x00\x00\x00WAVE"

    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}",
        headers={**OCTET, "Content-Length": "200001"},
        content=body(),
    )
    assert res.status_code == 413 and res.json()["error"]["code"] == "too_large"
    assert _files(settings) == []


def test_a_streamed_body_over_the_cap_is_cut_off_and_removed(settings) -> None:
    """Driven at the ASGI level: the test client would buffer the whole body first."""
    app = create_app(settings, RecordingDispatch())
    sent = []
    messages = []

    async def receive():
        if not sent:
            sent.append(1)
            return {"type": "http.request", "body": b"RIFF\x00\x00\x00\x00WAVE", "more_body": True}
        sent.append(1)
        return {"type": "http.request", "body": b"\x00" * 10_000, "more_body": len(sent) < 1000}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": "/v2/uploads",
        "raw_path": b"/v2/uploads",
        "query_string": f"cache_scope={SCOPE}".encode(),
        "root_path": "",
        "headers": [
            (b"authorization", AUTH["Authorization"].encode()),
            (b"content-type", b"application/octet-stream"),
            (b"idempotency-key", b"stream-1"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
    }
    anyio.run(app, scope, receive, send)
    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "too_large"
    assert len(sent) <= 22  # the server stopped reading just past the 200 kB cap
    assert _files(settings) == []


@pytest.mark.parametrize(
    "query,code",
    [
        ("", "scope_required"),
        ("?cache_scope=", "scope_required"),
        ("?cache_scope=has%20space", "invalid_request"),
        ("?cache_scope=" + "a" * 129, "invalid_request"),
        ("?cache_scope=a/b", "invalid_request"),
    ],
)
def test_a_valid_scope_is_required(settings, tmp_path: Path, query: str, code: str) -> None:
    res = client_for(settings).put(
        "/v2/uploads" + query, headers=OCTET, content=wav_bytes(tmp_path, 1.0)
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == code
    assert _files(settings) == []


def test_an_expired_upload_is_purged_and_cannot_be_used(settings, tmp_path: Path) -> None:
    client = client_for(settings)
    res = client.put(
        f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=wav_bytes(tmp_path, 1.0)
    )
    upload_id = res.json()["upload_id"]
    store = Store(settings.service_db_path)
    assert store.claim_upload(upload_id, SCOPE) is not None
    store.release_upload(upload_id)
    # Age it past its TTL.
    with store._conn() as conn:
        conn.execute("UPDATE uploads SET expires_at = ?", (time.time() - 1,))
    assert store.claim_upload(upload_id, SCOPE) is None
    purge_expired_uploads(settings, store)
    assert _files(settings) == [] and store.get_upload(upload_id) is None


def test_a_claimed_upload_whose_job_was_lost_is_purged_after_a_grace(settings, tmp_path) -> None:
    client = client_for(settings)
    res = client.put(
        f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=wav_bytes(tmp_path, 1.0)
    )
    upload_id = res.json()["upload_id"]
    store = Store(settings.service_db_path)
    assert store.claim_upload(upload_id, SCOPE) is not None
    with store._conn() as conn:
        conn.execute("UPDATE uploads SET expires_at = ?", (time.time() - 1,))
    purge_expired_uploads(settings, store)
    assert _files(settings) == [upload_id]  # its job may still be running
    with store._conn() as conn:
        conn.execute("UPDATE uploads SET expires_at = ?", (time.time() - 10_000,))
    purge_expired_uploads(settings, store)
    assert _files(settings) == []


def test_the_ttl_is_an_hour_by_default(tmp_path: Path) -> None:
    assert service_settings(tmp_path).upload_ttl_s == 3600
