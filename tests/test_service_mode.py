"""SERVICE_MODE: no UI, /files, /api/v1 or Spotify resolution, and no CORS. Standalone mode stays
as it was behind the flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import standalone
from app.config import Settings
from app.main import create_app

from .service_helpers import AUTH, client_for, service_settings
from .standalone_helpers import generated_token, signed_in, standalone_settings

STANDALONE_PATHS = [
    ("GET", "/"),
    ("GET", "/files/track-320.mp3"),
    ("GET", "/files/..%2Fservice_key"),
    ("GET", "/api/Gettrack?spotify_url=https://open.spotify.com/track/abc"),
    ("POST", "/api/v1/convert"),
    ("GET", "/api/v1/tasks/abc"),
    ("GET", "/api/v1/analyze/abc"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    # the UI page's own stylesheet and scripts
    *[("GET", f"/{name}") for name in standalone.UI_ASSETS],
]


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return service_settings(tmp_path)


@pytest.mark.parametrize("method,path", STANDALONE_PATHS)
def test_standalone_routes_are_off_in_service_mode(settings, method, path) -> None:
    settings.ensure_dirs()
    (settings.files_dir / "track-320.mp3").write_bytes(b"ID3audio")
    client = client_for(settings)
    for headers in ({}, AUTH):
        res = client.request(method, path, headers=headers, json={"source_url": "https://x"})
        assert res.status_code == 404, (path, res.status_code)
        assert b"ID3audio" not in res.content


def test_cors_is_off_in_service_mode(settings) -> None:
    client = client_for(settings)
    origin = {"Origin": "https://evil.example.net"}
    pre = client.options(
        "/v2/analyses",
        headers={**origin, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in pre.headers
    res = client.get("/healthz", headers=origin)
    assert "access-control-allow-origin" not in res.headers
    res = client.get("/v2/jobs/job_1", headers={**origin, **AUTH})
    assert "access-control-allow-origin" not in res.headers


def test_healthz_stays_open(settings) -> None:
    res = client_for(settings).get("/healthz")
    assert res.status_code == 200 and res.json()["ok"] is True


def test_a_bad_allowlist_entry_stops_the_service(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_app(service_settings(tmp_path, url_allowed_hosts="10.0.0.1"))


def test_standalone_mode_keeps_its_routes_behind_the_session(tmp_path: Path, monkeypatch) -> None:
    """Standalone mode still serves /files and the UI (now behind the ADR 0003 session, and with
    no CORS: the wildcard this test once asserted is the defect spotdown#52 removed)."""
    settings = standalone_settings(tmp_path)
    monkeypatch.setattr(standalone, "get_settings", lambda: settings)
    app = create_app(settings)
    (settings.files_dir / "track-320.mp3").write_bytes(b"ID3audio")
    with signed_in(app, generated_token(settings)) as client:
        res = client.get("/files/track-320.mp3", headers={"Origin": "https://ui.example.com"})
        assert res.status_code == 200 and res.content == b"ID3audio"
        assert "access-control-allow-origin" not in res.headers
        assert client.get("/").status_code == 200
        assert client.get("/v2/jobs/job_1", headers=AUTH).status_code == 404
    assert not settings.uploads_dir.exists()
