"""Every standalone route behind the session, and the defence in depth around it (ADR 0003
items 1, 3 and 6; spotdown#52 "Route table", "Exact exemptions", "Non-HTTP scopes", "No
lifespan", "Token failure", "Defence in depth", "Host", "Origin shape" and "No CORS")."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, List

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.routing import Route

from app.main import create_standalone_app

from .standalone_helpers import BASE, UI_HEADERS, Standalone, generated_token, standalone_settings

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = {("GET", "/healthz")}  # GET and POST /login are answered by the middleware, not a route
JSON = {"Content-Type": "application/json"}
SAME = {**UI_HEADERS, **JSON}


@pytest.fixture
def sa(tmp_path, monkeypatch) -> Standalone:
    return Standalone(tmp_path, monkeypatch)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _probes(app) -> List[tuple]:
    probes = []
    for route in app.routes:
        if not isinstance(route, (APIRoute, Route)):
            raise AssertionError(f"cannot probe route type {type(route).__name__}")
        for method in sorted(route.methods or ()):
            probes.append((method, route.path))
    return probes


# --- route table ----------------------------------------------------------------------------------


def test_every_route_needs_a_session(sa) -> None:
    probes = _probes(sa.app)
    paths = {p for _, p in probes}
    for needed in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json", "/healthz"):
        assert needed in paths, needed
    client = sa.anon()
    for method, path in probes:
        res = client.request(method, _concrete(path), headers=SAME, content=b"{}")
        if (method, path) in EXEMPT:
            assert res.status_code == 200, (method, path)
        else:
            assert res.status_code == 401, (method, path, res.status_code)
    browser = client.get("/", headers={"Accept": "text/html,*/*"}, follow_redirects=False)
    assert browser.status_code == 303 and browser.headers["location"] == "/login"
    assert client.get("/", headers={"Accept": "application/json"}).status_code == 401


def test_the_route_probe_refuses_route_types_it_cannot_probe(tmp_path) -> None:
    from starlette.routing import Mount, WebSocketRoute

    app = create_standalone_app(standalone_settings(tmp_path))
    app.router.routes.append(WebSocketRoute("/ws", lambda ws: None))
    with pytest.raises(AssertionError, match="WebSocketRoute"):
        _probes(app)
    app.router.routes[-1] = Mount("/m", routes=[])
    with pytest.raises(AssertionError, match="Mount"):
        _probes(app)


def test_effects_are_not_reached_without_a_session(sa) -> None:
    client = sa.anon()
    res = client.post("/api/v1/convert", json={"source_url": "https://e.example/a.wav"},
                      headers=UI_HEADERS)  # fmt: skip
    assert res.status_code == 401 and sa.queue.jobs == []
    assert client.get("/api/Gettrack?spotify_url=x", headers=UI_HEADERS).status_code == 401
    assert client.get("/api/v1/analyze/x?refresh=true", headers=UI_HEADERS).status_code == 401
    assert sa.spotify_calls == [] and sa.analyses == []


# --- exact exemptions -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/login/"), ("GET", "/LOGIN"), ("GET", "//login"), ("GET", "/healthz/"),
     ("HEAD", "/healthz"), ("POST", "/healthz"), ("GET", "/nope"), ("GET", "/api/nope"),
     ("POST", "/LOGIN"), ("GET", "/logout"), ("POST", "/logout")],
)  # fmt: skip
def test_variants_are_not_exempt(sa, method, path) -> None:
    assert sa.anon().request(method, path, headers=SAME).status_code == 401


def test_a_percent_encoded_login_is_login(sa) -> None:
    res = sa.anon().get("/%6Cogin")
    assert res.status_code == 200 and "<form" in res.text


# --- non-HTTP scopes ------------------------------------------------------------------------------


def _drive(app, scope: dict) -> list:
    sent: list = []

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_a_websocket_is_closed_with_1008_before_accept(sa) -> None:
    scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", b"localhost")],
             "query_string": b"", "root_path": "", "scheme": "ws"}  # fmt: skip
    assert _drive(sa.app, scope) == [{"type": "websocket.close", "code": 1008}]


def test_an_unknown_scope_type_is_refused(sa) -> None:
    with pytest.raises(RuntimeError, match="refuses"):
        _drive(sa.app, {"type": "webtransport", "path": "/"})


# --- no lifespan ----------------------------------------------------------------------------------


async def _no_lifespan(app, cookie: str = "") -> List[int]:
    transport = httpx.ASGITransport(app=app)  # runs no lifespan
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        statuses = [(await client.get("/ui.css")).status_code]
        if cookie:
            res = await client.get("/ui.css", headers={"Cookie": cookie})
            statuses.append(res.status_code)
    return statuses


def _cookie_for(app, token: str) -> str:
    from fastapi.testclient import TestClient

    from .standalone_helpers import session_cookie

    return session_cookie(TestClient(app, base_url=BASE), token)


def test_a_constructed_app_authenticates_without_a_lifespan(tmp_path, monkeypatch) -> None:
    sa = Standalone(tmp_path, monkeypatch)
    assert sa.settings.files_dir.is_dir() and sa.settings.tmp_dir.is_dir()  # ensure_dirs ran
    assert asyncio.run(_no_lifespan(sa.app, _cookie_for(sa.app, sa.token))) == [401, 200]
    owner = tmp_path / "short"
    owner.write_text("too-short")
    broken = Standalone(tmp_path / "b", monkeypatch, standalone_access_token_file=owner)
    assert asyncio.run(_no_lifespan(broken.app)) == [503]


def test_the_module_app_authenticates_without_a_lifespan() -> None:
    import app.main as main

    settings = main.get_settings()
    assert settings.files_dir.is_dir()  # ensure_dirs ran at import
    cookie = _cookie_for(main.app, generated_token(settings))
    assert asyncio.run(_no_lifespan(main.app, cookie)) == [401, 200]


def test_the_module_app_with_a_broken_token_is_503(tmp_path) -> None:
    owner = tmp_path / "owner"
    owner.write_text("x" * 31)
    script = (
        "import asyncio, httpx, app.main as m\n"
        "async def go():\n"
        "    t = httpx.ASGITransport(app=m.app)\n"
        "    async with httpx.AsyncClient(transport=t, base_url='http://localhost:8000') as c:\n"
        "        print((await c.get('/ui.css')).status_code)\n"
        "asyncio.run(go())\n"
    )
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path / "d"),
        "STANDALONE_ACCESS_TOKEN_FILE": str(owner),
    }
    run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=120)  # fmt: skip
    assert run.returncode == 0, run.stderr[-2000:]
    assert run.stdout.strip().splitlines()[-1] == "503"
    assert "length" in run.stdout and "x" * 31 not in run.stdout


# --- token failure --------------------------------------------------------------------------------


def _owner_cases(tmp_path: Path) -> dict:
    empty, short, folder = tmp_path / "empty", tmp_path / "short", tmp_path / "folder"
    empty.write_text("")
    short.write_text("x" * 31)
    folder.mkdir()
    return {"empty": empty, "short": short, "unreadable": folder}


@pytest.mark.parametrize("case", ["empty", "short", "unreadable", "not writable"])
def test_no_valid_token_is_503_everywhere_but_healthz(tmp_path, monkeypatch, case) -> None:
    from app import token_store

    from .token_race_helpers import OsProxy

    over: dict[str, Any] = {}
    if case == "not writable":

        def before(op: str, args: tuple) -> None:
            if op == "open":
                raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(token_store, "os", OsProxy(before))
    else:
        over["standalone_access_token_file"] = _owner_cases(tmp_path)[case]
    sa = Standalone(tmp_path / "app", monkeypatch, **over)
    client = sa.anon()
    probes = _probes(sa.app) + [("GET", "/login"), ("POST", "/login"), ("POST", "/logout")]
    for method, path in probes:
        res = client.request(method, _concrete(path), headers=SAME, content=b'{"token":"x"}')
        want = 200 if (method, path) == ("GET", "/healthz") else 503
        assert res.status_code == want, (method, path, res.status_code)
    assert client.get("/healthz").json() == {"ok": True, "redis": False}


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_a_read_only_data_dir_is_503(tmp_path, monkeypatch) -> None:
    settings = standalone_settings(tmp_path)
    settings.ensure_dirs()
    settings.data_dir.chmod(0o500)
    try:
        sa = Standalone(tmp_path, monkeypatch)
        assert sa.anon().get("/ui.css").status_code == 503
    finally:
        settings.data_dir.chmod(0o700)


# --- defence in depth (with a valid session) ------------------------------------------------------


CONVERT = {"source_url": "https://e.example/a.wav"}


def test_the_right_request_reaches_convert(sa) -> None:
    with sa.signed() as client:
        res = client.post("/api/v1/convert", json=CONVERT)
        assert res.status_code == 200 and len(sa.queue.jobs) == 1


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "http://localhost:9999", "null", "http://localhost"]
)
def test_convert_needs_the_exact_origin(sa, origin) -> None:
    with sa.signed() as client:
        client.headers.pop("Origin")
        headers = {"Origin": origin} if origin else {}
        res = client.post("/api/v1/convert", json=CONVERT, headers=headers)
        assert res.status_code == 403 and sa.queue.jobs == []


def test_a_forged_forwarded_proto_does_not_change_the_expected_origin(sa) -> None:
    with sa.signed() as client:
        headers = {"Origin": "https://localhost:8000", "X-Forwarded-Proto": "https",
                   "X-Forwarded-Host": "localhost:8000", "Forwarded": "proto=https"}  # fmt: skip
        assert client.post("/api/v1/convert", json=CONVERT, headers=headers).status_code == 403
    assert sa.queue.jobs == []


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_get_the_origin_check(sa, method) -> None:
    with sa.signed() as client:
        res = client.request(method, "/api/v1/convert", headers={"Origin": "https://evil.example",
                             **JSON}, content=b"{}")  # fmt: skip
        assert res.status_code == 403


@pytest.mark.parametrize(
    "ctype", [None, "text/plain", "application/x-www-form-urlencoded",
              "multipart/form-data; boundary=x", "application/json; charset=latin-1"],
)  # fmt: skip
def test_convert_needs_the_exact_json_type(sa, ctype) -> None:
    with sa.signed() as client:
        headers = {"Content-Type": ctype} if ctype else {}
        res = client.post("/api/v1/convert", content=b'{"source_url":"x"}', headers=headers)
        assert res.status_code == 415 and sa.queue.jobs == []


@pytest.mark.parametrize("method", ["DELETE", "PUT", "POST", "PATCH"])
def test_an_unsafe_request_to_an_unlisted_route_is_415(sa, method) -> None:
    with sa.signed() as client:
        res = client.request(method, "/api/v1/tasks/x", content=b"x", headers={"Content-Type":
                             "text/plain"})  # fmt: skip
        assert res.status_code == 415
        res = client.request(method, "/api/v1/tasks/x", content=b"{}", headers=JSON)
        assert res.status_code == 415  # no listed type for this route at all: default deny


def test_api_requests_need_x_spotdown_ui(sa) -> None:
    with sa.signed() as client:
        client.headers.pop("X-Spotdown-UI")
        assert client.get("/api/v1/tasks/x").status_code == 403
        assert client.get("/api/v1/tasks/x", headers={"X-Spotdown-UI": "true"}).status_code == 403
        assert client.post("/api/v1/convert", json=CONVERT).status_code == 403
        assert sa.queue.jobs == []
        assert client.get("/ui.css").status_code == 200  # only /api/ needs it


@pytest.mark.parametrize(
    "origin", ["http://localhost:8000/", "http://u@localhost:8000", "http://localhost:8000?x"]
)
def test_an_origin_with_a_path_userinfo_or_query_never_matches(sa, origin) -> None:
    with sa.signed() as client:
        res = client.post("/api/v1/convert", json=CONVERT, headers={"Origin": origin})
        assert res.status_code == 403 and sa.queue.jobs == []


# --- Host -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api", "/api/v1/tasks/x", "/files/a.mp3", "/login",
                                  "/healthz"])  # fmt: skip
def test_a_foreign_host_is_403_even_with_a_session(sa, path) -> None:
    with sa.signed() as client:
        assert client.get(path, headers={"Host": "evil.example"}).status_code == 403
        assert client.post("/login", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "[::1]",
                                  "localhost", "LOCALHOST:8000"])  # fmt: skip
def test_the_default_hosts_are_served(sa, host) -> None:
    with sa.signed() as client:
        assert client.get("/ui.css", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["", "::1", "[::1", "local host", "localhost:x", "a@localhost",
                                  "localhost:8000:1", "localhost.", "127.0.0.2"])  # fmt: skip
def test_a_malformed_or_unlisted_host_is_403(sa, host) -> None:
    with sa.signed() as client:
        assert client.get("/ui.css", headers={"Host": host}).status_code == 403


def test_a_repeated_host_header_is_403(sa) -> None:
    scope_headers = [(b"host", b"localhost"), (b"host", b"evil.example")]
    sent = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/healthz", "headers": scope_headers,
             "query_string": b"", "root_path": "", "scheme": "http",
             "http_version": "1.1"}  # fmt: skip
    asyncio.run(sa.app(scope, receive, send))
    assert sent[0]["status"] == 403


# --- no CORS --------------------------------------------------------------------------------------


def test_no_route_answers_with_cors_headers(sa) -> None:
    preflight = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "x-spotdown-ui,content-type"}  # fmt: skip
    for client in (sa.anon(), sa.signed()):
        with client:
            for method, path in _probes(sa.app) + [("OPTIONS", "/api/v1/convert")]:
                for headers in ({"Origin": "https://evil.example"}, preflight):
                    res = client.request(method, _concrete(path), headers=headers)
                    leaked = [h for h in res.headers if h.lower().startswith("access-control-")]
                    assert leaked == [], (method, path, leaked)
    assert not any("CORS" in type(m.cls).__name__ + str(m.cls) for m in sa.app.user_middleware)


# --- service mode ---------------------------------------------------------------------------------


def test_service_mode_has_none_of_this(tmp_path) -> None:
    from app.main import create_app
    from app.standalone_auth import StandaloneAuth

    from .service_helpers import client_for, service_settings

    settings = service_settings(tmp_path)
    app = create_app(settings)
    assert all(m.cls is not StandaloneAuth for m in app.user_middleware)
    assert not settings.standalone_token_path.exists()
    assert list(settings.data_dir.glob("standalone_token*")) == []
    res = client_for(settings).get("/healthz")
    assert res.status_code == 200 and set(res.json()) == {"ok", "redis"}


@pytest.mark.parametrize("host", ["[::1]:8000", "127.0.0.1:8000", "LocalHost:8000"])
def test_the_expected_origin_follows_the_allowed_host(sa, host) -> None:
    with sa.signed() as client:
        origin = "http://" + host.lower()
        res = client.post("/api/v1/convert", json=CONVERT, headers={"Host": host, "Origin": origin})
        assert res.status_code == 200
        res = client.post("/api/v1/convert", json=CONVERT, headers={"Host": host})  # localhost
        assert res.status_code == (200 if host.lower() == "localhost:8000" else 403)
