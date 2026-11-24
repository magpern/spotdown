"""Two caller keys (ADR 0005, Decision 3): classbeat's and the UI's, the same rights on /v2, both
checked on every request, and a start-up that fails closed on a bad or duplicate UI key."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import errors, service_key, spotify_routes
from app.service.service_key import ServiceKey, ServiceKeyError, ServiceKeys, load_service_keys

from .service_helpers import TEST_KEY, client_for, service_settings

UI_KEY = "ui-caller-key-7c1e9a3b5d2f4680b1c2d3e4f5a6b7c8"
KEYS = {"classbeat": TEST_KEY, "ui": UI_KEY}
CALLS = [
    ("GET", "/v2/jobs/job_1?cache_scope=scope_a", 404),
    ("POST", "/v2/spotify/resolve", 422),  # invalid_request: past the key check
    ("POST", "/v2/match", 422),
]


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture()
def settings(tmp_path: Path):
    ui = tmp_path / "ui_key"
    ui.write_text(UI_KEY + "\n", encoding="utf-8")
    return service_settings(tmp_path, ui_service_key_file=ui)


def _bad_headers() -> list:
    out = [{}, _bearer("wrong-key-wrong-key-wrong-key-wrong-key")]
    for key in KEYS.values():
        out += [_bearer(key[:-1]), _bearer(key[1:]), _bearer(key + "x"), _bearer("x" + key)]
        out += [_bearer(key[:32]), {"Authorization": key}, {"Authorization": f"Basic {key}"}]
    out.append(_bearer(TEST_KEY + UI_KEY))
    return out


@pytest.mark.parametrize("who", sorted(KEYS))
@pytest.mark.parametrize("method,path,status", CALLS)
def test_each_key_passes_the_guard(settings, who, method, path, status) -> None:
    res = client_for(settings).request(method, path, headers=_bearer(KEYS[who]), json={"url": "x"})
    assert res.status_code == status, res.text
    assert res.json()["error"]["code"] != "unauthorized"


@pytest.mark.parametrize("method,path,status", CALLS)
@pytest.mark.parametrize("headers", _bad_headers())
def test_neither_key_is_401(settings, method, path, status, headers) -> None:
    res = client_for(settings).request(method, path, headers=headers, json={"url": "x"})
    assert res.status_code == 401
    assert res.json() == {"error": {"code": "unauthorized", "message": "unauthorized"}}


def test_without_a_ui_key_only_classbeats_key_passes(tmp_path: Path) -> None:
    client = client_for(service_settings(tmp_path))
    assert client.get("/v2/jobs/job_1", headers=_bearer(UI_KEY)).status_code == 401
    assert client.get("/v2/jobs/job_1", headers=_bearer(TEST_KEY)).status_code == 404


@pytest.mark.parametrize("who", sorted(KEYS))
def test_the_new_route_checks_the_key_without_the_middleware(settings, who) -> None:
    """Defence in depth: the resolve router refuses on its own, and takes either key."""
    app = FastAPI()
    app.state.settings = settings
    app.state.service_key = load_service_keys(
        settings.service_key_file, settings.ui_service_key_file
    )
    app.state.spotify = object()  # never reached: the link "x" is refused first
    errors.install_handlers(app)
    app.include_router(spotify_routes.router)
    client = TestClient(app)
    assert client.post("/v2/spotify/resolve", json={"url": "x"}).status_code == 401
    bad = _bearer(KEYS[who][:-1])
    assert client.post("/v2/spotify/resolve", headers=bad, json={"url": "x"}).status_code == 401
    res = client.post("/v2/spotify/resolve", headers=_bearer(KEYS[who]), json={"url": "x"})
    assert res.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("presented", [TEST_KEY, UI_KEY, "neither-key-neither-key-neither"])
def test_every_key_is_compared_without_stopping_at_a_match(monkeypatch, presented) -> None:
    calls = []
    real = service_key.hmac.compare_digest

    def spy(a, b):
        calls.append(b)
        return real(a, b)

    monkeypatch.setattr(service_key.hmac, "compare_digest", spy)
    keys = ServiceKeys([ServiceKey(TEST_KEY), ServiceKey(UI_KEY)])
    calls.clear()  # the duplicate check at construction compares too
    assert keys.matches(presented) is (presented in KEYS.values())
    assert calls == [hashlib.sha256(k.encode()).digest() for k in (TEST_KEY, UI_KEY)]


def test_the_keys_are_never_in_a_repr(settings) -> None:
    keys = load_service_keys(settings.service_key_file, settings.ui_service_key_file)
    assert len(keys) == 2
    for text in (repr(keys), str(keys)):
        assert TEST_KEY not in text and UI_KEY not in text
    assert not hasattr(keys, "__dict__")


def _ui_file(tmp_path: Path, case: str) -> Path:
    path = tmp_path / "ui_key_case"
    if case == "missing":
        return tmp_path / "no-such-file"
    if case == "directory":
        path.mkdir()
    elif case == "short":
        path.write_text("short-ui-secret-under-32", encoding="utf-8")
    elif case == "empty":
        path.write_text("  \n", encoding="utf-8")
    elif case == "not_utf8":
        path.write_bytes(b"\xff\xfe" + b"k" * 40)
    elif case == "equal":
        path.write_text(f"  {TEST_KEY}\n\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("case", ["missing", "directory", "short", "empty", "not_utf8", "equal"])
def test_start_up_fails_closed_on_a_bad_ui_key(tmp_path: Path, caplog, case) -> None:
    caplog.set_level(logging.DEBUG)
    settings = service_settings(tmp_path, ui_service_key_file=_ui_file(tmp_path, case))
    with pytest.raises(ServiceKeyError) as caught:
        create_app(settings)
    message = str(caught.value)
    assert "UI_SERVICE_KEY_FILE" in message or "differ" in message
    for secret in (TEST_KEY, "short-ui-secret-under-32"):
        assert secret not in message and secret not in caplog.text
        assert secret not in repr(caught.value)


def test_two_equal_keys_are_refused_in_any_order() -> None:
    with pytest.raises(ServiceKeyError):
        ServiceKeys([ServiceKey(UI_KEY), ServiceKey(UI_KEY)])
    with pytest.raises(ServiceKeyError):
        ServiceKeys([])
