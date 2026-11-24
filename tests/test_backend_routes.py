"""The backend is API only (ADR 0005 Decision 2, spotdown#79): ``/v2/...`` behind a caller key
and ``/healthz``; every other path is 404 with or without a key; no CORS and no API docs.
``SERVICE_MODE``, ``SERVE_UI`` and ``SPOTDOWN_UI`` are ignored with one warning naming them, and
without a readable service key the api does not start, whatever they say."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

import pytest

from app.config import Settings
from app.main import create_app
from app.service.service_key import ServiceKeyError

from .service_helpers import AUTH, client_for, service_settings

DEPRECATED = ["SERVICE_MODE", "SERVE_UI", "SPOTDOWN_UI"]
# Values a stale compose or .env file may hold; the last is a marker that must never be logged.
VALUES = ["true", "false", "on", "off", "", "yes", "not-a-boolean-71c3"]

# Paths the retired standalone app, its UI and the service page answered, API docs, files under
# the data directory, and look-alikes of /v2 and /healthz. Every one is 404, key or no key.
GONE: List[Tuple[str, str]] = [
    ("GET", "/"),
    ("GET", "/index.html"),
    ("GET", "/service.html"),
    ("GET", "/ui.js"),
    ("GET", "/ui.css"),
    ("GET", "/service_ui.js"),
    ("GET", "/waveform_model.js"),
    ("GET", "/files/track-320.mp3"),
    ("GET", "/files/..%2Fservice.db"),
    ("GET", "/service.db"),
    ("GET", "/api/Gettrack?spotify_url=https://open.spotify.com/track/abc"),
    ("POST", "/api/v1/convert"),
    ("GET", "/api/v1/tasks/abc"),
    ("GET", "/api/v1/analyze/abc"),
    ("POST", "/api/v1/analyze-upload"),
    ("GET", "/api/spotify/resolve"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/v2x/jobs/job_1"),
    ("GET", "/V2/jobs/job_1"),
    ("GET", "/healthz/x"),
    ("GET", "/static/ui.js"),
]
V2 = [
    ("GET", "/v2"),
    ("GET", "/v2/jobs/job_1"),
    ("GET", "/v2/analyses/an_1"),
    ("PUT", "/v2/uploads?cache_scope=spotdown_ui"),
    ("POST", "/v2/analyses"),
    ("POST", "/v2/spotify/resolve"),
    ("POST", "/v2/match"),
    ("POST", "/v2/audio"),
    ("GET", "/v2/audio/au_1"),
    ("DELETE", "/v2/scopes/spotdown_ui"),
    ("GET", "/v2/ui.css"),
]


def _sub(tmp_path: Path, name: str) -> Path:
    (tmp_path / name).mkdir()
    return tmp_path / name


def _paths(settings: Settings) -> List[str]:
    return sorted(getattr(r, "path", "") for r in create_app(settings).router.routes)


def test_only_v2_and_healthz_are_routed(tmp_path: Path) -> None:
    paths = _paths(service_settings(tmp_path))
    assert "/healthz" in paths
    assert all(p == "/healthz" or p.startswith("/v2/") for p in paths), paths


def test_every_other_path_is_404_with_or_without_a_key(tmp_path: Path) -> None:
    settings = service_settings(tmp_path)
    client = client_for(settings)
    (settings.data_dir / "files").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "files" / "track-320.mp3").write_bytes(b"ID3audio")
    for method, path in GONE:
        for headers in ({}, AUTH):
            res = client.request(method, path, headers=headers)
            assert res.status_code == 404, (method, path, bool(headers), res.status_code)
            assert b"ID3audio" not in res.content
            assert "access-control-allow-origin" not in res.headers


def test_v2_needs_a_key(tmp_path: Path) -> None:
    client = client_for(service_settings(tmp_path))
    for method, path in V2:
        res = client.request(method, path)
        assert res.status_code == 401, (method, path, res.status_code)
        assert res.json()["error"]["code"] == "unauthorized"
    assert client.get("/v2/jobs/job_1", headers=AUTH).status_code == 404


def test_there_is_no_cors(tmp_path: Path) -> None:
    client = client_for(service_settings(tmp_path))
    origin = {"Origin": "https://evil.example.net"}
    pre = client.options(
        "/v2/analyses", headers={**origin, **AUTH, "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in pre.headers
    for path, headers in [("/healthz", origin), ("/v2/jobs/job_1", {**origin, **AUTH})]:
        assert "access-control-allow-origin" not in client.get(path, headers=headers).headers


def test_healthz_stays_open(tmp_path: Path) -> None:
    res = client_for(service_settings(tmp_path)).get("/healthz")
    assert res.status_code == 200 and res.json()["ok"] is True


@pytest.mark.parametrize("name", DEPRECATED)
@pytest.mark.parametrize("value", VALUES)
def test_a_deprecated_setting_changes_nothing_and_warns_once_without_its_value(
    tmp_path: Path, monkeypatch, caplog, name: str, value: str
) -> None:
    baseline = _paths(service_settings(_sub(tmp_path, "a")))
    monkeypatch.setenv(name, value)
    caplog.set_level(logging.DEBUG)
    settings = service_settings(_sub(tmp_path, "b"))
    assert settings.deprecated_set() == [name]
    assert _paths(settings) == baseline
    warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert warnings[0].getMessage().endswith(": " + name)
    assert "not-a-boolean-71c3" not in caplog.text
    client = client_for(service_settings(_sub(tmp_path, "c")))
    assert client.get("/").status_code == 404 and client.get("/v2/jobs/j").status_code == 401


def test_all_three_set_warn_once_and_name_each(tmp_path: Path, monkeypatch, caplog) -> None:
    for name in DEPRECATED:
        monkeypatch.setenv(name, "true")
    caplog.set_level(logging.WARNING)
    create_app(service_settings(tmp_path))
    warnings = [r.getMessage() for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].endswith(": SERVICE_MODE, SERVE_UI, SPOTDOWN_UI")


def test_unset_deprecated_settings_log_nothing(tmp_path: Path, monkeypatch, caplog) -> None:
    for name in DEPRECATED:
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.WARNING)
    create_app(service_settings(tmp_path))
    assert not [r for r in caplog.records if "deprecated" in r.getMessage()]


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"SERVICE_MODE": "false"},
        {"SERVE_UI": "true", "SPOTDOWN_UI": "on"},
        {"SERVICE_MODE": ""},
    ],
)
@pytest.mark.parametrize("key", ["missing", "empty", "unset"])
def test_without_a_readable_key_the_api_does_not_start(
    tmp_path: Path, monkeypatch, env, key: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    settings = service_settings(tmp_path)
    if key == "missing":
        settings.service_key_file = tmp_path / "missing"
    elif key == "empty":
        Path(str(settings.service_key_file)).write_text("  \n", encoding="utf-8")
    else:
        settings.service_key_file = None
    with pytest.raises(ServiceKeyError):
        create_app(settings)


def test_the_api_starts_without_a_ui_key_or_spotify_credentials(tmp_path: Path) -> None:
    settings = service_settings(tmp_path)
    assert settings.ui_service_key_file is None and settings.spotify_client_id == ""
    assert settings.spotify_client_secret_file is None
    assert client_for(settings).get("/healthz").status_code == 200


def test_ensure_dirs_makes_only_what_the_backend_uses(tmp_path: Path) -> None:
    settings = service_settings(tmp_path)
    settings.ensure_dirs()
    made = sorted(p.name for p in settings.data_dir.iterdir())
    assert made == ["audio", "tmp", "uploads", "work"]


def test_importing_the_app_module_needs_no_key(tmp_path: Path, monkeypatch) -> None:
    """``app.main`` builds its ``app`` on first access only, so the worker and the tests import it
    without a key file; the attribute itself fails closed."""
    import app.main as main

    assert "app" not in vars(main)
    keyless = Settings(data_dir=tmp_path / "data", service_key_file=None)
    monkeypatch.setattr(main, "get_settings", lambda: keyless)
    with pytest.raises(ServiceKeyError):
        main.app  # noqa: B018
    assert "app" not in vars(main)
    with pytest.raises(AttributeError):
        main.not_an_attribute  # noqa: B018
