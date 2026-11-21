"""POST /api/v1/analyze-upload: the session, the cross-site checks, no CORS, where it is registered
and that upload results are not readable through GET /api/v1/analyze (ADR 0002, spotdown#49).

The session, ``Host``, ``Origin``, content-type and ``X-Spotdown-UI`` checks are ADR 0003's auth
middleware (the real one, signed in through the real ``POST /login``); the route adds the
declared-length cap. Every check runs before the slot and before any body byte; a refused request
writes nothing.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any, List

import httpx
import pytest

pytest.importorskip("fcntl")

from fastapi.testclient import TestClient  # noqa: E402

from app import analyze, db, upload_route, upload_slot  # noqa: E402
from app.main import create_app  # noqa: E402

from . import media_fixtures as mf  # noqa: E402
from .upload_helpers import (  # noqa: E402
    BASE,
    GOOD,
    LOCK,
    PATH,
    fake_sandbox,
    left,
    make_app,
    standalone_settings,
)

WAV = mf.wav(seconds=0.5)


@pytest.fixture()
def settings(tmp_path: Path):
    return standalone_settings(tmp_path)


class Unread:
    """A streamed body that records whether the server pulled any of it (the ASGI transport
    reads the body only when the app asks for it)."""

    def __init__(self) -> None:
        self.read = False

    async def __aiter__(self):
        self.read = True
        yield WAV


def _post(app: Any, headers: dict, body: Any = WAV, base: str = BASE) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base) as client:
            return await client.post(PATH, headers=headers, content=body)

    return asyncio.run(go())


# --- the session --------------------------------------------------------------------------------


def test_without_a_session_it_is_401_and_nothing_is_read_or_taken(settings, monkeypatch) -> None:
    app = make_app(settings, monkeypatch, session=False)
    taken: List[Any] = []
    real = upload_slot.try_acquire
    monkeypatch.setattr(upload_slot, "try_acquire", lambda d: taken.append(d) or real(d))
    body = Unread()
    res = _post(app, GOOD, body)
    assert res.status_code == 401
    assert not body.read and taken == [] and left(settings) == [LOCK]


def test_the_401_holds_through_asgi_transport_without_lifespan(settings, monkeypatch) -> None:
    app = make_app(settings, monkeypatch, session=False)

    assert _post(app, GOOD).status_code == 401  # httpx.ASGITransport, no lifespan
    assert left(settings) == [LOCK]


def test_a_broken_token_is_503_and_nothing_is_read_or_taken(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "owner_token"
    bad.write_text("short\n", encoding="utf-8")  # breaks the owner-token rule (length)
    settings = standalone_settings(tmp_path, standalone_access_token_file=bad)
    app = make_app(settings, monkeypatch, session=False)
    taken: List[Any] = []
    real = upload_slot.try_acquire
    monkeypatch.setattr(upload_slot, "try_acquire", lambda d: taken.append(d) or real(d))
    body = Unread()
    assert _post(app, GOOD, body).status_code == 503
    assert not body.read and taken == [] and left(settings) == [LOCK]


def test_a_forged_or_stale_cookie_is_401(settings, monkeypatch) -> None:
    from .upload_helpers import with_cookie

    app = make_app(settings, monkeypatch, session=False)
    forged = with_cookie(app, "spotdown_session=99999999999." + "0" * 64)
    assert _post(forged, GOOD).status_code == 401 and left(settings) == [LOCK]


def test_the_availability_probe_needs_the_session_too(settings, monkeypatch) -> None:
    client = TestClient(make_app(settings, monkeypatch, session=False), base_url=BASE)
    assert client.get(PATH, headers={"X-Spotdown-UI": "1"}).status_code == 401
    client = TestClient(make_app(settings, monkeypatch), base_url=BASE)
    assert client.get(PATH, headers={"X-Spotdown-UI": "1"}).json() == {"available": True}


# --- cross-site (with a session) ----------------------------------------------------------------

REFUSED = [
    ("foreign_origin", {**GOOD, "Origin": "http://evil.example"}, None, 403),
    ("other_port", {**GOOD, "Origin": "http://localhost:9999"}, None, 403),
    ("no_origin", {k: v for k, v in GOOD.items() if k != "Origin"}, None, 403),
    ("null_origin", {**GOOD, "Origin": "null"}, None, 403),
    (
        "forged_proto",
        {**GOOD, "Origin": "https://localhost:8000", "X-Forwarded-Proto": "https"},
        None,
        403,
    ),
    ("no_ui_header", {k: v for k, v in GOOD.items() if k != "X-Spotdown-UI"}, None, 403),
    ("ui_header_0", {**GOOD, "X-Spotdown-UI": "0"}, None, 403),
    ("other_host", {**GOOD, "Origin": "http://evil.example:8000"}, "evil.example:8000", 403),
    ("text_plain", {**GOOD, "Content-Type": "text/plain"}, None, 415),
    ("form", {**GOOD, "Content-Type": "application/x-www-form-urlencoded"}, None, 415),
    ("multipart", {**GOOD, "Content-Type": "multipart/form-data; boundary=x"}, None, 415),
    ("octet_with_params", {**GOOD, "Content-Type": "application/octet-stream; x=1"}, None, 415),
    ("no_type", {k: v for k, v in GOOD.items() if k != "Content-Type"}, None, 415),
]


@pytest.mark.parametrize("name,headers,host,code", REFUSED, ids=[r[0] for r in REFUSED])
def test_each_cross_site_check_refuses_before_the_body(
    settings, monkeypatch, name, headers, host, code
) -> None:
    calls: List[Path] = []
    fake_sandbox(monkeypatch, calls)
    app = make_app(settings, monkeypatch)
    headers = dict(headers)
    if host:
        headers["Host"] = host
    body = Unread()
    res = _post(app, headers, body)
    assert res.status_code == code
    assert "access-control-allow-origin" not in res.headers
    assert not body.read and calls == [] and left(settings) == [LOCK]


def test_the_good_headers_pass_so_each_refusal_is_its_check(settings, monkeypatch) -> None:
    fake_sandbox(monkeypatch)
    res = _post(make_app(settings, monkeypatch), GOOD)
    assert res.status_code == 200, res.text
    assert "access-control-allow-origin" not in res.headers
    assert left(settings) == [LOCK]


def test_the_public_origin_is_the_only_one_accepted(tmp_path: Path, monkeypatch) -> None:
    fake_sandbox(monkeypatch)
    settings = standalone_settings(
        tmp_path, ui_public_origin="https://spotdown.example", ui_allowed_hosts="spotdown.example"
    )
    base = "https://spotdown.example"
    app = make_app(settings, monkeypatch, base=base)
    http = _post(app, {**GOOD, "Origin": "http://spotdown.example"}, base=base)
    assert http.status_code == 403
    ok = _post(app, {**GOOD, "Origin": "https://spotdown.example"}, base=base)
    assert ok.status_code == 200, ok.text


def test_a_preflight_gets_no_cors_headers(settings, monkeypatch) -> None:
    client = TestClient(make_app(settings, monkeypatch), base_url=BASE)
    for origin in ("http://evil.example", "http://localhost:8000"):
        pre = client.options(
            PATH,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-spotdown-ui",
            },
        )
        assert not [h for h in pre.headers if h.lower().startswith("access-control-")]


def test_the_route_has_its_one_content_type_in_the_default_deny_table() -> None:
    from app.session_policy import OCTET_STREAM, UNSAFE_CONTENT_TYPES

    assert UNSAFE_CONTENT_TYPES[("POST", PATH)] == OCTET_STREAM
    assert ("GET", PATH) not in UNSAFE_CONTENT_TYPES


def test_a_declared_over_size_length_is_413_unread(settings, monkeypatch) -> None:
    """The route's own check: the middleware does not cap the body."""
    app = make_app(settings, monkeypatch)
    for declared in (str(settings.max_upload_bytes + 1), "12x"):
        body = Unread()
        res = _post(app, {**GOOD, "Content-Length": declared}, body)
        assert res.status_code == 413
        assert not body.read and left(settings) == [LOCK]


# --- upload results -----------------------------------------------------------------------------


def test_an_upload_result_is_not_readable_through_get_analyze(settings, monkeypatch) -> None:
    app = make_app(settings, monkeypatch)
    track_id = "upl_" + "ab" * 32
    db.set_analysis(track_id, analyze.ANALYZER_VERSION, analyze.V1_RESPONSE_VERSION, '{"x": 1}')
    read: List[Any] = []
    monkeypatch.setattr(db, "get_analysis", lambda *a: read.append(a))
    client = TestClient(app, base_url=BASE)
    for tid in (track_id, track_id.upper().replace("UPL_", "Upl_")):
        res = client.get(f"/api/v1/analyze/{tid}", headers={"X-Spotdown-UI": "1"})
        assert res.status_code == 404
    assert read == []


# --- registration -------------------------------------------------------------------------------


def _paths(app: Any) -> List[str]:
    return [getattr(r, "path", "") for r in app.routes]


def test_not_registered_in_service_mode_or_without_the_ui(tmp_path: Path) -> None:
    from .service_helpers import service_settings

    assert PATH not in _paths(create_app(service_settings(tmp_path)))
    off = standalone_settings(tmp_path, serve_ui=False)
    assert PATH not in _paths(create_app(off))
    assert PATH in _paths(create_app(standalone_settings(tmp_path)))


def test_not_registered_without_fcntl(tmp_path: Path) -> None:
    real = sys.modules.get("fcntl")
    sys.modules["fcntl"] = None  # type: ignore[assignment] - the import now raises ImportError
    try:
        slot = importlib.reload(upload_slot)
        assert slot.AVAILABLE is False
        assert upload_route.available(standalone_settings(tmp_path)) is False
        assert PATH not in _paths(create_app(standalone_settings(tmp_path)))
    finally:
        sys.modules["fcntl"] = real  # type: ignore[assignment]
        importlib.reload(upload_slot)
    assert upload_slot.AVAILABLE is True


def test_a_bad_host_setting_stops_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_app(standalone_settings(tmp_path, ui_allowed_hosts="*"))
    with pytest.raises(ValueError):
        create_app(standalone_settings(tmp_path, ui_public_origin="https://x.example/"))


def test_not_registered_without_resource(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "resource", None)  # no sandbox rlimits: no route
    assert upload_route.available(standalone_settings(tmp_path)) is False


def test_a_superscript_declared_length_is_413_not_500(settings, monkeypatch) -> None:
    app = make_app(settings, monkeypatch)
    body = Unread()
    res = _post(app, {**GOOD, "Content-Length": b"\xb2"}, body)  # type: ignore[dict-item]
    assert res.status_code == 413 and not body.read and left(settings) == [LOCK]
