"""Every v2 call without the service key is 401; the key is compared in constant time and
never logged, printed or echoed."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import errors, routes, service_key
from app.service.service_key import ServiceKey, ServiceKeyError, load_service_key

from .service_helpers import (
    AUTH,
    SCOPE,
    TEST_KEY,
    RecordingDispatch,
    client_for,
    service_settings,
    wav_bytes,
)

V2_CALLS = [
    ("PUT", "/v2/uploads?cache_scope=scope_a"),
    ("POST", "/v2/analyses"),
    ("GET", "/v2/jobs/job_0123"),
    ("GET", "/v2/jobs/job_0123?cache_scope=scope_a"),
    ("GET", "/v2/no-such-route"),
    ("DELETE", "/v2/uploads"),
    ("GET", "/v2"),
]
BAD_HEADERS = [
    {},
    {"Authorization": "Bearer wrong-key-wrong-key-wrong-key-wrong"},
    {"Authorization": f"Basic {TEST_KEY}"},
    {"Authorization": TEST_KEY},
    {"Authorization": "Bearer "},
    {"Authorization": f"Bearer {TEST_KEY}x"},
    {"Authorization": f"Bearer {TEST_KEY[:-1]}"},
    {"X-Service-Key": TEST_KEY},
]


@pytest.fixture()
def settings(tmp_path: Path):
    return service_settings(tmp_path)


@pytest.mark.parametrize("method,path", V2_CALLS)
@pytest.mark.parametrize("headers", BAD_HEADERS)
def test_every_v2_call_without_the_key_is_401(settings, method, path, headers) -> None:
    dispatch = RecordingDispatch()
    client = client_for(settings, dispatch)
    body = b'{"cache_scope": "scope_a", "upload_id": "up_x"}'
    res = client.request(method, path, headers=headers, content=body)
    assert res.status_code == 401
    assert res.json() == {"error": {"code": "unauthorized", "message": "unauthorized"}}
    assert res.headers["www-authenticate"] == "Bearer"
    assert dispatch.job_ids == []
    assert not any(settings.uploads_dir.iterdir())


def test_two_authorization_headers_are_refused(settings) -> None:
    client = client_for(settings)
    res = client.get(
        "/v2/jobs/job_1",
        headers=[("Authorization", f"Bearer {TEST_KEY}"), ("Authorization", "Bearer x")],
    )
    assert res.status_code == 401


def test_the_right_key_passes(settings) -> None:
    client = client_for(settings)
    res = client.get("/v2/jobs/job_1?cache_scope=scope_a", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    res = client.get("/v2/jobs/job_1", headers={"Authorization": f"bearer  {TEST_KEY} "})
    assert res.status_code == 404


def test_routes_check_the_key_without_the_middleware(settings) -> None:
    """Defence in depth: the router's own dependency refuses too."""
    app = FastAPI()
    app.state.settings = settings
    app.state.service_key = load_service_key(settings.service_key_file)
    errors.install_handlers(app)
    app.include_router(routes.router)
    client = TestClient(app)
    for method, path in V2_CALLS[:4]:
        assert client.request(method, path).status_code == 401


def test_the_comparison_is_constant_time_on_digests(settings, monkeypatch) -> None:
    calls = []
    real = service_key.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(service_key.hmac, "compare_digest", spy)
    key = ServiceKey(TEST_KEY)
    assert key.matches(TEST_KEY)
    assert not key.matches("short")
    assert len(calls) == 2
    for a, b in calls:
        assert isinstance(a, bytes) and isinstance(b, bytes) and len(a) == len(b) == 32
    assert calls[0][1] == hashlib.sha256(TEST_KEY.encode()).digest()


def test_the_key_is_never_in_a_repr_or_an_error(tmp_path: Path) -> None:
    key = ServiceKey(TEST_KEY)
    assert TEST_KEY not in repr(key) and TEST_KEY not in str(key)
    assert not hasattr(key, "__dict__")
    short = tmp_path / "short"
    short.write_text("too-short-secret", encoding="utf-8")
    with pytest.raises(ServiceKeyError) as caught:
        load_service_key(short)
    assert "too-short-secret" not in str(caught.value)


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_service_mode_fails_closed_without_a_key(tmp_path: Path, content) -> None:
    settings = service_settings(tmp_path)
    if content is None:
        settings.service_key_file = tmp_path / "missing"
    else:
        Path(str(settings.service_key_file)).write_text(content, encoding="utf-8")
    with pytest.raises(ServiceKeyError):
        create_app(settings)
    settings.service_key_file = None
    with pytest.raises(ServiceKeyError):
        create_app(settings)


def test_the_key_is_never_logged_or_echoed(settings, tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client = client_for(settings)
    seen = []
    res = client.put(
        f"/v2/uploads?cache_scope={SCOPE}",
        headers={**AUTH, "Content-Type": "application/octet-stream"},
        content=wav_bytes(tmp_path),
    )
    seen.append(res)
    upload_id = res.json()["upload_id"]
    seen.append(
        client.post(
            "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
        )
    )
    seen.append(
        client.get(f"/v2/jobs/{seen[-1].json()['job_id']}?cache_scope={SCOPE}", headers=AUTH)
    )
    seen.append(client.get("/v2/jobs/x", headers={"Authorization": f"Bearer {TEST_KEY}x"}))
    seen.append(client.post("/v2/analyses", headers=AUTH, json={"cache_scope": "bad scope!"}))
    for res in seen:
        assert TEST_KEY not in res.text
        assert all(TEST_KEY not in v for v in res.headers.values())
    assert TEST_KEY not in caplog.text
    assert TEST_KEY not in repr(settings.model_dump())


def test_unknown_routes_and_methods_use_the_error_envelope(settings) -> None:
    client = client_for(settings)
    missing = client.get("/v2/no-such-route", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "not_found", "message": "not found"}}
    wrong = client.delete("/v2/uploads", headers=AUTH)
    assert wrong.status_code == 405
    assert wrong.json() == {
        "error": {"code": "method_not_allowed", "message": "method not allowed"}
    }
    assert "PUT" in wrong.headers["allow"]


def test_an_unknown_field_name_is_not_echoed(settings) -> None:
    body = {"cache_scope": SCOPE, "upload_id": "up_1", "SECRETFIELD": 1}
    res = client_for(settings).post("/v2/analyses", headers=AUTH, json=body)
    assert res.status_code == 422
    assert res.json()["error"] == {"code": "invalid_request", "message": "invalid fields: body"}
    assert "SECRETFIELD" not in res.text


def test_the_middleware_guards_v2_behind_a_root_path() -> None:
    """With ``--root-path /prefix`` the router sees ``/v2/...``; so does the key check."""
    reached = []

    async def inner(scope, receive, send) -> None:
        reached.append(scope["path"])

    sent = []

    async def send(message) -> None:
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    middleware = service_key.ServiceKeyMiddleware(inner, ServiceKey(TEST_KEY))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/prefix/v2/jobs/job_1",
        "root_path": "/prefix",
        "headers": [],
    }
    asyncio.run(middleware(scope, receive, send))
    assert reached == []
    assert sent[0]["status"] == 401
    asyncio.run(middleware({**scope, "path": "/prefix/healthz"}, receive, send))
    assert reached == ["/prefix/healthz"]
