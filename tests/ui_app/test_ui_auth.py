"""ADR 0003's test table carried over and run against the UI app (spotdown#78 acceptance 3): every
route except ``/login`` and ``/healthz`` needs the session, the exemptions are exact, the ``Host``
allowlist and the same-origin checks hold, there is no CORS and no API docs, and every response
carries ADR 0004 Decision 6's headers."""

from __future__ import annotations

import asyncio
import re
from typing import List

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Route

from spotdown_ui.headers import CSP

from .helpers import BASE, UI_HEADERS, Ui

JSON = {"Content-Type": "application/json"}
SAME = {**UI_HEADERS, **JSON}
EXEMPT = {("GET", "/healthz")}  # GET and POST /login are answered by the middleware


@pytest.fixture
def ui(tmp_path) -> Ui:
    return Ui(tmp_path)


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


def test_every_route_needs_a_session(ui) -> None:
    probes = _probes(ui.app)
    paths = {p for _, p in probes}
    assert {"/api/spotify/resolve", "/api/match", "/api/uploads", "/api/analyses",
            "/api/jobs/{ident}", "/api/analyses/{ident}", "/api/audio", "/api/audio/{ident}",
            "/", "/{name}", "/healthz"} <= paths  # fmt: skip
    client = ui.anon()
    for method, path in probes:
        res = client.request(method, _concrete(path), headers=SAME, content=b"{}")
        want = 200 if (method, path) in EXEMPT else 401
        assert res.status_code == want, (method, path, res.status_code)
    browser = client.get("/", headers={"Accept": "text/html,*/*"}, follow_redirects=False)
    assert browser.status_code == 303 and browser.headers["location"] == "/login"
    assert ui.backend.seen == []


def test_there_are_no_api_docs(ui) -> None:
    paths = {p for _, p in _probes(ui.app)}
    assert not {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"} & paths
    with ui.signed() as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/login/"), ("GET", "/LOGIN"), ("GET", "//login"), ("GET", "/healthz/"),
     ("HEAD", "/healthz"), ("POST", "/healthz"), ("GET", "/nope"), ("GET", "/api/nope"),
     ("POST", "/LOGIN"), ("GET", "/logout"), ("POST", "/logout"), ("PUT", "/api/uploads"),
     ("GET", "/api/audio/au_1")],
)  # fmt: skip
def test_variants_are_not_exempt(ui, method, path) -> None:
    assert ui.anon().request(method, path, headers=SAME).status_code == 401


def test_a_websocket_is_closed_with_1008_before_accept(ui) -> None:
    sent: list = []

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", b"localhost")],
             "query_string": b"", "root_path": "", "scheme": "ws"}  # fmt: skip
    asyncio.run(ui.app(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


# --- token failure --------------------------------------------------------------------------------


def test_an_unusable_owner_token_is_503_everywhere_but_healthz(tmp_path) -> None:
    short = tmp_path / "short"
    short.write_text("x" * 31)
    ui = Ui(tmp_path, UI_ACCESS_TOKEN_FILE=str(short))
    client = ui.anon()
    for method, path in _probes(ui.app) + [("GET", "/login"), ("POST", "/login")]:
        res = client.request(method, _concrete(path), headers=SAME, content=b'{"token":"x"}')
        want = 200 if (method, path) == ("GET", "/healthz") else 503
        assert res.status_code == want, (method, path, res.status_code)


def test_an_owner_token_file_signs_in(tmp_path) -> None:
    owner = tmp_path / "owner"
    owner.write_text("owner-token-0123456789-0123456789-xyz\n")
    ui = Ui(tmp_path, UI_ACCESS_TOKEN_FILE=str(owner))
    with ui.signed() as client:
        assert client.get("/ui.css").status_code == 200
    assert not (tmp_path / "data" / "ui_token").exists()


def test_a_wrong_token_is_401_and_the_rate_limit_closes(ui) -> None:
    client = ui.anon()
    headers = {**UI_HEADERS, **JSON}
    for _ in range(5):
        assert client.post("/login", json={"token": "wrong"}, headers=headers).status_code == 401
    res = client.post("/login", json={"token": ui.token}, headers=headers)
    assert res.status_code == 429 and "retry-after" in res.headers


def test_logout_clears_the_cookie(ui) -> None:
    with ui.signed() as client:
        res = client.post("/logout", json={})
        assert res.status_code == 204 and "Max-Age=0" in res.headers["set-cookie"]


# --- defence in depth (with a valid session) ------------------------------------------------------

UNSAFE = [("POST", "/api/spotify/resolve", JSON), ("POST", "/api/match", JSON),
          ("POST", "/api/analyses", JSON), ("POST", "/api/audio", JSON),
          ("PUT", "/api/uploads", {"Content-Type": "application/octet-stream"})]  # fmt: skip


@pytest.mark.parametrize("method,path,ctype", UNSAFE)
@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "http://localhost:9999", "null", "http://localhost",
               "http://localhost:8000/", "http://u@localhost:8000"],
)  # fmt: skip
def test_unsafe_requests_need_the_exact_origin(ui, method, path, ctype, origin) -> None:
    with ui.signed() as client:
        client.headers.pop("Origin")
        headers = {**ctype, **({"Origin": origin} if origin else {})}
        assert client.request(method, path, content=b"{}", headers=headers).status_code == 403
    assert ui.backend.seen == []


def test_a_forged_forwarded_proto_does_not_change_the_expected_origin(ui) -> None:
    with ui.signed() as client:
        headers = {"Origin": "https://localhost:8000", "X-Forwarded-Proto": "https",
                   "X-Forwarded-Host": "localhost:8000", "Forwarded": "proto=https"}  # fmt: skip
        assert client.post("/api/match", json={}, headers=headers).status_code == 403
    assert ui.backend.seen == []


@pytest.mark.parametrize("method,path,ctype", UNSAFE)
@pytest.mark.parametrize(
    "wrong", [None, "text/plain", "application/x-www-form-urlencoded",
              "multipart/form-data; boundary=x", "application/json; charset=latin-1"],
)  # fmt: skip
def test_unsafe_requests_need_their_one_content_type(ui, method, path, ctype, wrong) -> None:
    if wrong == ctype["Content-Type"]:
        return
    with ui.signed() as client:
        headers = {"Content-Type": wrong} if wrong else {}
        assert client.request(method, path, content=b"{}", headers=headers).status_code == 415
    assert ui.backend.seen == []


@pytest.mark.parametrize("method", ["DELETE", "PUT", "POST", "PATCH"])
def test_an_unsafe_request_to_an_unlisted_route_is_415(ui, method) -> None:
    with ui.signed() as client:
        res = client.request(method, "/api/jobs/x", content=b"{}", headers=JSON)
        assert res.status_code == 415


def test_api_requests_need_x_spotdown_ui(ui) -> None:
    with ui.signed() as client:
        client.headers.pop("X-Spotdown-UI")
        assert client.get("/api/jobs/job_1").status_code == 403
        assert client.get("/api/jobs/job_1", headers={"X-Spotdown-UI": "true"}).status_code == 403
        assert client.post("/api/match", json={}).status_code == 403
        assert client.get("/ui.css").status_code == 200  # only /api/ needs it
    assert ui.backend.seen == []


# --- Host -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/jobs/job_1", "/login", "/healthz", "/ui.css"])
def test_a_foreign_host_is_403_even_with_a_session(ui, path) -> None:
    with ui.signed() as client:
        assert client.get(path, headers={"Host": "evil.example"}).status_code == 403
        assert client.post("/login", headers={"Host": "evil.example"}).status_code == 403
    assert ui.backend.seen == []


@pytest.mark.parametrize("host", ["", "::1", "[::1", "local host", "localhost:x", "a@localhost",
                                  "localhost:8000:1", "localhost.", "127.0.0.2"])  # fmt: skip
def test_a_malformed_or_unlisted_host_is_403(ui, host) -> None:
    with ui.signed() as client:
        assert client.get("/ui.css", headers={"Host": host}).status_code == 403


def test_configured_hosts_and_public_origin(tmp_path) -> None:
    ui = Ui(tmp_path, UI_ALLOWED_HOSTS="ui.example", UI_PUBLIC_ORIGIN="https://ui.example")
    client = ui.anon(base_url="http://ui.example")
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz", headers={"Host": "localhost"}).status_code == 403
    login = {"X-Spotdown-UI": "1", "Origin": "https://ui.example", **JSON}
    res = client.post("/login", json={"token": ui.token}, headers=login)
    assert res.status_code == 204
    assert (
        res.headers["set-cookie"].startswith("__Host-spotdown_session=")
        and "Secure" in res.headers["set-cookie"]
    )
    res = client.post(
        "/login", json={"token": ui.token}, headers={**login, "Origin": "http://ui.example"}
    )
    assert res.status_code == 403


# --- no CORS, and the headers ---------------------------------------------------------------------


def test_no_route_answers_with_cors_headers(ui) -> None:
    preflight = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "x-spotdown-ui,content-type"}  # fmt: skip
    for client in (ui.anon(), ui.signed()):
        with client:
            for method, path in _probes(ui.app) + [("OPTIONS", "/api/match")]:
                for headers in ({"Origin": "https://evil.example"}, preflight):
                    res = client.request(method, _concrete(path), headers=headers)
                    leaked = [h for h in res.headers if h.lower().startswith("access-control-")]
                    assert leaked == [], (method, path, leaked)
    assert not any("CORS" in str(m.cls) for m in ui.app.user_middleware)


def test_every_response_carries_the_adr_0004_headers(ui) -> None:
    responses = [
        ui.anon().get("/"),
        ui.anon().get("/healthz"),
        ui.anon().get("/x", headers={"Host": "evil"}),
    ]
    with ui.signed() as client:
        responses += [client.get("/"), client.get("/ui_link.js"), client.get("/api/jobs/job_1"),
                      client.get("/api/audio/au_1"), client.get("/nope")]  # fmt: skip
    for res in responses:
        assert res.headers["content-security-policy"] == CSP, res.request.url
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert res.headers["cache-control"] == "no-store"
    login = ui.anon().get("/login")
    assert login.headers["content-security-policy"].startswith(
        "default-src 'none'; style-src 'sha256-"
    )
    assert login.headers["x-frame-options"] == "DENY"


def test_the_page_has_no_inline_script_and_loads_only_listed_assets(ui) -> None:
    from spotdown_ui.routes import ASSETS

    with ui.signed() as client:
        page = client.get("/").text
        for name in ASSETS:
            res = client.get("/" + name)
            assert res.status_code == 200 and res.headers["content-type"].startswith(ASSETS[name])
    scripts = re.findall(r"<script([^>]*)>", page)
    assert scripts and all('src="' in s for s in scripts)
    referenced = set(re.findall(r'(?:src|href)="([^"]+)"', page))
    assert referenced == set(ASSETS), referenced ^ set(ASSETS)
    assert BASE not in page
