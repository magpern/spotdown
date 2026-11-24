"""SERVICE_MODE: no /files, /api/v1 or Spotify resolution, and no CORS; the static UI page only with
SPOTDOWN_UI=on (ADR 0004), whose switch is independent of the mode. Standalone mode stays as it was
behind its flag."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import service_ui, standalone
from app.config import Settings
from app.main import create_app

from .service_helpers import AUTH, client_for, service_settings
from .standalone_helpers import generated_token, signed_in, standalone_settings

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
SERVICE_UI_ASSETS = service_ui.SERVICE_UI_ASSETS

# Every UI path either mode serves: the page, the standalone page's assets and the service page's.
UI_PATHS = sorted(
    {"/", *(f"/{n}" for n in standalone.UI_ASSETS), *(f"/{n}" for n in SERVICE_UI_ASSETS)}
)
# Never served in service mode, whatever the UI switch says.
STANDALONE_PATHS = [
    ("GET", "/files/track-320.mp3"),
    ("GET", "/files/..%2Fservice_key"),
    ("GET", "/api/Gettrack?spotify_url=https://open.spotify.com/track/abc"),
    ("POST", "/api/v1/convert"),
    ("GET", "/api/v1/tasks/abc"),
    ("GET", "/api/v1/analyze/abc"),
    ("GET", "/api/v1/analyze-upload"),
    ("POST", "/api/v1/analyze-upload"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("GET", "/index.html"),
    ("GET", "/service.html"),
    ("GET", "/track-320.mp3"),  # a single-segment name of a file under data/files
    ("GET", "/service_key"),
]

# The UI switch in service mode: (settings overrides, whether the UI is served). SERVE_UI is the
# standalone switch and never turns the UI on in service mode (classbeat's deployment unchanged).
SERVICE_SWITCH = [
    ({}, False),
    ({"spotdown_ui": False}, False),
    ({"serve_ui": True}, False),
    ({"serve_ui": True, "spotdown_ui": False}, False),
    ({"spotdown_ui": True}, True),
    ({"spotdown_ui": True, "serve_ui": False}, True),
]
# In standalone mode: SPOTDOWN_UI wins, else SERVE_UI (default on, as before).
STANDALONE_SWITCH = [
    ({}, True),
    ({"spotdown_ui": True}, True),
    ({"serve_ui": False}, False),
    ({"spotdown_ui": False}, False),
    ({"spotdown_ui": False, "serve_ui": True}, False),
    ({"spotdown_ui": True, "serve_ui": False}, True),
]


def _service_expected(ui_on: bool, method: str, path: str) -> int:
    """The status a path answers in service mode (no key needed for any of these)."""
    if ui_on and method == "GET" and (path == "/" or path[1:] in SERVICE_UI_ASSETS):
        return 200
    return 404


@pytest.mark.parametrize("overrides,ui_on", SERVICE_SWITCH)
def test_service_mode_serves_exactly_the_ui_its_switch_allows(tmp_path, overrides, ui_on) -> None:
    """The table: every UI path and every standalone path, with and without the key."""
    settings = service_settings(tmp_path, **overrides)
    assert settings.ui_enabled is ui_on
    settings.ensure_dirs()
    (settings.files_dir / "track-320.mp3").write_bytes(b"ID3audio")
    client = client_for(settings)
    rows = [("GET", p) for p in UI_PATHS] + STANDALONE_PATHS
    for method, path in rows:
        want = _service_expected(ui_on, method, path)
        for headers in ({}, AUTH):
            res = client.request(method, path, headers=headers, json={"source_url": "https://x"})
            if want == 404 and method != "GET" and ui_on:
                # a single-segment POST meets the GET-only asset route: refused all the same
                assert res.status_code in (404, 405), (overrides, method, path, res.status_code)
            else:
                assert res.status_code == want, (overrides, method, path, res.status_code)
            assert b"ID3audio" not in res.content
            assert "access-control-allow-origin" not in res.headers
            if want == 200:
                name = "service.html" if path == "/" else path[1:]
                assert res.content == (STATIC / name).read_bytes(), path
                assert res.headers["content-security-policy"] == service_ui.CSP
                assert res.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("overrides,ui_on", SERVICE_SWITCH)
def test_v2_needs_the_key_whatever_the_ui_switch(tmp_path, overrides, ui_on) -> None:
    client = client_for(service_settings(tmp_path, **overrides))
    for method, path in [
        ("GET", "/v2/jobs/job_1"),
        ("GET", "/v2/analyses/an_1"),
        ("PUT", "/v2/uploads?cache_scope=spotdown_ui"),
        ("POST", "/v2/analyses"),
        ("GET", "/v2"),
        ("GET", "/v2/ui.css"),
    ]:
        res = client.request(method, path)
        assert res.status_code == 401, (overrides, path, res.status_code)
        assert "ID3" not in res.text
    assert client.get("/v2/jobs/job_1", headers=AUTH).status_code == 404


def test_the_service_page_loads_only_its_own_assets() -> None:
    html = (STATIC / "service.html").read_text(encoding="utf-8")
    loaded = set(re.findall(r'(?:src|href)="([^"]+)"', html))
    assert loaded == set(SERVICE_UI_ASSETS), loaded ^ set(SERVICE_UI_ASSETS)
    assert "<script>" not in html and "on" + "click" not in html  # no inline script (CSP)


def test_the_asset_route_comes_after_every_fixed_route(tmp_path) -> None:
    app = create_app(service_settings(tmp_path, spotdown_ui=True))
    paths = [getattr(r, "path", "") for r in app.router.routes]
    catch_all = paths.index("/{name}")
    assert paths.index("/healthz") < catch_all
    assert all(not p.startswith("/v2") for p in paths[catch_all:])


@pytest.mark.parametrize("overrides,ui_on", STANDALONE_SWITCH)
def test_standalone_mode_serves_the_ui_its_switch_allows(
    tmp_path, monkeypatch, overrides, ui_on
) -> None:
    settings = standalone_settings(tmp_path, **overrides)
    assert settings.ui_enabled is ui_on
    monkeypatch.setattr(standalone, "get_settings", lambda: settings)
    app = create_app(settings)
    with signed_in(app, generated_token(settings)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert ("analysis_view.js" in page.text) is ui_on
        for path in UI_PATHS[1:]:
            res = client.get(path)
            want = 200 if ui_on and path[1:] in standalone.UI_ASSETS else 404
            assert res.status_code == want, (overrides, path, res.status_code)
        assert client.get("/v2/jobs/job_1", headers=AUTH).status_code == 404


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return service_settings(tmp_path)


@pytest.mark.parametrize("ui", [None, False, True])
def test_cors_is_off_in_service_mode(tmp_path: Path, ui) -> None:
    client = client_for(service_settings(tmp_path, spotdown_ui=ui))
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
    for path in ("/", "/service_ui.js"):
        assert "access-control-allow-origin" not in client.get(path, headers=origin).headers


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
