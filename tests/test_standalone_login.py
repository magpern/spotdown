"""Login, logout, the cookie and the login page (ADR 0003 item 4; spotdown#52 "Login", "Cookie",
"Login page", "No leaks" and the Additions on rate-limit counting and UTF-8 tokens)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
from typing import Iterator, List

import httpx
import pytest

from app import session_policy, standalone_auth

from .standalone_helpers import BASE, LOGIN_HEADERS, Standalone

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, monkeypatch) -> None:
        self.t = NOW
        monkeypatch.setattr(standalone_auth, "_now", lambda: self.t)


@pytest.fixture
def sa(tmp_path, monkeypatch) -> Standalone:
    return Standalone(tmp_path, monkeypatch)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


def _login(client, token: str, **headers):
    return client.post("/login", json={"token": token}, headers={**LOGIN_HEADERS, **headers})


def _close_the_limit(client) -> None:
    for _ in range(5):
        assert _login(client, "wrong").status_code == 401


@pytest.fixture
def evaluated(monkeypatch) -> List[str]:
    calls: List[str] = []
    real = standalone_auth.token_matches
    monkeypatch.setattr(
        standalone_auth, "token_matches", lambda s, t: calls.append(s) or real(s, t)
    )
    return calls


# --- check order and the rate limit ---------------------------------------------------------------


def test_a_closed_limit_is_429_even_for_the_right_token(sa, clock, evaluated) -> None:
    client = sa.anon()
    _close_the_limit(client)
    evaluated.clear()
    res = _login(client, sa.token)
    assert res.status_code == 429 and res.headers["retry-after"] == "60"
    assert "set-cookie" not in res.headers and evaluated == []
    clock.t += 59
    assert _login(client, sa.token).status_code == 429  # attempts while closed do not count
    clock.t += 1
    assert _login(client, sa.token).status_code == 204  # the window did not move


def test_the_limit_is_global_across_peers(sa, clock) -> None:
    async def go() -> List[int]:
        out = []
        for i in range(6):
            peer = (f"10.0.0.{i % 2 + 1}", 40000 + i)
            transport = httpx.ASGITransport(app=sa.app, client=peer)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                token = sa.token if i == 5 else "wrong"
                res = await client.post("/login", json={"token": token}, headers=LOGIN_HEADERS)
                out.append(res.status_code)
        return out

    assert asyncio.run(go()) == [401] * 5 + [429]
    clock.t += 60
    assert _login(sa.anon(), sa.token).status_code == 204  # open again after the window


def test_four_failures_leave_the_limit_open(sa, clock) -> None:
    client = sa.anon()
    for _ in range(4):
        assert _login(client, "wrong").status_code == 401
    assert _login(client, sa.token).status_code == 204


def test_failures_before_step_9_do_not_count(sa, clock, evaluated) -> None:
    client = sa.anon()
    big = json.dumps({"token": "x" * 2000})
    attempts = [
        dict(headers={**LOGIN_HEADERS, "Origin": "https://evil.example"}, content=b'{"token":"w"}'),
        dict(headers={**LOGIN_HEADERS, "X-Spotdown-UI": "0"}, content=b'{"token":"w"}'),
        dict(headers={**LOGIN_HEADERS, "Content-Type": "text/plain"}, content=b'{"token":"w"}'),
        dict(headers=LOGIN_HEADERS, content=big.encode()),
        dict(headers=LOGIN_HEADERS, content=b"{not json"),
    ]
    # Five of each: if any one kind were counted, five of it would close the limit.
    statuses = [client.post("/login", **a).status_code for a in attempts * 5]
    assert statuses == [403, 403, 415, 413, 400] * 5 and evaluated == []
    assert _login(client, sa.token).status_code == 204


@pytest.mark.parametrize(
    "headers,status",
    [({"Origin": None}, 403), ({"Origin": "https://evil.example"}, 403),
     ({"Origin": "http://localhost:8000/"}, 403), ({"X-Spotdown-UI": None}, 403),
     ({"Content-Type": "text/plain"}, 415), ({"Content-Type": None}, 415),
     ({"Content-Type": "application/x-www-form-urlencoded"}, 415)],
)  # fmt: skip
def test_login_needs_the_origin_the_header_and_the_type(sa, headers, status, evaluated) -> None:
    sent = {k: v for k, v in {**LOGIN_HEADERS, **headers}.items() if v is not None}
    res = asyncio.run(_post(sa, sent, b'{"token":"x"}'))
    assert res.status_code == status and evaluated == []


async def _post(sa, headers: dict, body) -> httpx.Response:
    transport = httpx.ASGITransport(app=sa.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        return await client.post("/login", headers=headers, content=body)


# --- body -----------------------------------------------------------------------------------------


def test_a_body_over_1_kib_is_413_by_content_length(sa, evaluated) -> None:
    body = json.dumps({"token": sa.token, "pad": "x" * 1024}).encode()
    res = sa.anon().post("/login", content=body, headers=LOGIN_HEADERS)
    assert res.status_code == 413 and evaluated == []


def test_a_body_over_1_kib_is_413_while_streaming(sa, evaluated) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"token":"' + sa.token.encode() + b'","pad":"'
        for _ in range(10):
            yield b"x" * 200
        yield b'"}'

    res = sa.anon().post("/login", content=chunks(), headers=LOGIN_HEADERS)
    assert "content-length" not in {k.lower() for k in res.request.headers}
    assert res.status_code == 413 and evaluated == []


@pytest.mark.parametrize(
    "body", [b"[]", b'"x"', b"1", b"null", b"{}", b'{"token": 5}', b'{"token": null}',
             b'{"token": ["x"]}', b"not json", b"\xff\xfe", b"", b"[" * 600 + b"]" * 400],
)  # fmt: skip
def test_a_malformed_body_is_400(sa, body, evaluated) -> None:
    res = sa.anon().post("/login", content=body, headers=LOGIN_HEADERS)
    assert res.status_code in (400, 413) and evaluated == []
    if len(body) <= 1024:
        assert res.status_code == 400


def test_a_wrong_token_is_401_without_a_cookie(sa) -> None:
    res = _login(sa.anon(), "w" * 64)
    assert res.status_code == 401 and "set-cookie" not in res.headers


# --- UTF-8 and length -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ['{"token":"' + "é" * 40 + '"}', '{"token":"\\ud800\\udfff\\ud800"}',
             '{"token":"' + "x" * 513 + '"}'],
)  # fmt: skip
def test_odd_submitted_tokens_are_401_never_500(sa, clock, body) -> None:
    client = sa.anon()
    res = client.post("/login", content=body.encode(), headers=LOGIN_HEADERS)
    assert res.status_code == 401
    for _ in range(4):
        client.post("/login", content=body.encode(), headers=LOGIN_HEADERS)
    assert _login(client, sa.token).status_code == 429  # each counted as a failure


def test_a_512_character_owner_token_logs_in(tmp_path, monkeypatch) -> None:
    allowed = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in '"\\')
    token = (allowed * 6)[:512]
    owner = tmp_path / "owner"
    owner.write_text(token + "\n")
    sa = Standalone(tmp_path / "a", monkeypatch, standalone_access_token_file=owner)
    body = json.dumps({"token": token}, separators=(",", ":")).encode()
    assert len(body) <= 1024
    res = sa.anon().post("/login", content=body, headers=LOGIN_HEADERS)
    assert res.status_code == 204
    longer = json.dumps({"token": token + "x"}).encode()
    assert sa.anon().post("/login", content=longer, headers=LOGIN_HEADERS).status_code == 401


@pytest.mark.parametrize(
    "content,rule",
    [("x" * 40 + "é", "character set"), ("x" * 20 + " " + "x" * 20, "character set"),
     ("x" * 40 + '"', "character set"), ("x" * 40 + "\\", "character set"),
     ("x" * 31, "length"), ("x" * 513, "length")],
)  # fmt: skip
def test_an_owner_token_outside_the_rule_is_503(tmp_path, monkeypatch, capsys, content, rule):
    owner = tmp_path / "owner"
    owner.write_text(content, encoding="utf-8")
    sa = Standalone(tmp_path / "a", monkeypatch, standalone_access_token_file=owner)
    out = capsys.readouterr().out
    assert f"owner-token rule: {rule}" in out and content not in out
    assert sa.anon().get("/ui.css").status_code == 503
    assert _login(sa.anon(), content).status_code == 503


def test_an_owner_file_fixed_live_is_picked_up(tmp_path, monkeypatch) -> None:
    from app import token_store

    t = [100.0]
    monkeypatch.setattr(token_store, "_monotonic", lambda: t[0])
    owner = tmp_path / "owner"
    owner.write_text("short")
    sa = Standalone(tmp_path / "a", monkeypatch, standalone_access_token_file=owner)
    client = sa.anon()
    assert client.get("/ui.css").status_code == 503
    owner.write_text("k" * 40)
    t[0] += 1.0
    assert _login(client, "k" * 40).status_code == 204


# --- the cookie -----------------------------------------------------------------------------------


def test_the_cookie_over_https(tmp_path, monkeypatch) -> None:
    sa = Standalone(tmp_path, monkeypatch, ui_public_origin="https://spot.example",
                    ui_allowed_hosts="spot.example")  # fmt: skip
    client = sa.anon("https://spot.example")
    res = _login(client, sa.token, Origin="https://spot.example")
    assert res.status_code == 204
    cookie = res.headers["set-cookie"]
    name_value, *attrs = [p.strip() for p in cookie.split(";")]
    assert name_value.startswith("__Host-spotdown_session=")
    assert attrs == ["Max-Age=43200", "Path=/", "Secure", "HttpOnly", "SameSite=Strict"]
    assert "domain" not in cookie.lower()
    fresh = sa.anon("https://spot.example")  # no cookie jar: only the header below is sent
    assert fresh.get("/ui.css", headers={"Cookie": name_value}).status_code == 200
    plain = name_value.replace("__Host-", "")
    assert fresh.get("/ui.css", headers={"Cookie": plain}).status_code == 401
    assert _login(client, sa.token, Origin="http://spot.example").status_code == 403


def test_the_cookie_over_http(sa) -> None:
    res = _login(sa.anon(), sa.token)
    name_value, *attrs = [p.strip() for p in res.headers["set-cookie"].split(";")]
    assert re.fullmatch(r"spotdown_session=[0-9]{1,12}\.[0-9a-f]{64}", name_value)
    assert attrs == ["Max-Age=43200", "Path=/", "HttpOnly", "SameSite=Strict"]


def _mac(token: str, expiry: str) -> str:
    key = hmac.new(token.encode(), b"spotdown-session-v1", hashlib.sha256).digest()
    return hmac.new(key, expiry.encode(), hashlib.sha256).hexdigest()


def test_bad_cookies_are_401(sa, clock) -> None:
    token, now = sa.token, int(NOW)
    good = f"{now + 60}.{_mac(token, str(now + 60))}"
    client = sa.anon()
    assert client.get("/ui.css", headers={"Cookie": f"spotdown_session={good}"}).status_code == 200
    bad = [
        f"{now - 1}.{_mac(token, str(now - 1))}",
        f"{now + 43201}.{_mac(token, str(now + 43201))}",
        f"12a.{_mac(token, '12a')}",
        f"{'1' * 13}.{_mac(token, '1' * 13)}",
        f"{now + 60}.{_mac(token, str(now + 61))}",
        f"{now + 60}.{_mac(token, str(now + 60))[:-1]}",
        f"{now + 60}.{_mac(token, str(now + 60))}0",
    ]
    for value in bad:
        assert (
            client.get("/ui.css", headers={"Cookie": f"spotdown_session={value}"}).status_code
            == 401
        )
    assert client.get("/ui.css", headers={"Cookie": f"other={good}"}).status_code == 401


def test_a_cookie_from_before_a_rotation_is_401(tmp_path, monkeypatch) -> None:
    sa = Standalone(tmp_path, monkeypatch)
    old = sa.token
    cookie = _login(sa.anon(), old).headers["set-cookie"].split(";")[0]
    assert sa.anon().get("/ui.css", headers={"Cookie": cookie}).status_code == 200
    sa.settings.standalone_token_path.unlink()  # rotation: delete and restart
    rotated = Standalone(tmp_path, monkeypatch)
    assert rotated.token != old
    assert rotated.anon().get("/ui.css", headers={"Cookie": cookie}).status_code == 401


def test_the_session_mac_is_compared_with_compare_digest(sa, monkeypatch) -> None:
    cookie = _login(sa.anon(), sa.token).headers["set-cookie"].split(";")[0]
    mac = cookie.split(".")[1].encode()
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert sa.anon().get("/ui.css", headers={"Cookie": cookie}).status_code == 200
    assert (mac, mac) in calls
    assert session_policy.hmac is hmac


# --- logout ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("https", [False, True])
def test_logout_clears_the_cookie(tmp_path, monkeypatch, https) -> None:
    origin = "https://spot.example" if https else BASE
    over = {"ui_public_origin": origin, "ui_allowed_hosts": "spot.example"} if https else {}
    sa = Standalone(tmp_path, monkeypatch, **over)
    client = sa.anon(origin)
    cookie = _login(client, sa.token, Origin=origin).headers["set-cookie"].split(";")[0]
    headers = {**LOGIN_HEADERS, "Origin": origin, "Cookie": cookie}
    res = client.post("/logout", content=b"{}", headers=headers)
    assert res.status_code == 204
    name = "__Host-spotdown_session" if https else "spotdown_session"
    want = [f"{name}=", "Max-Age=0", "Path=/"] + (["Secure"] if https else [])
    assert [p.strip() for p in res.headers["set-cookie"].split(";")] == want + [
        "HttpOnly", "SameSite=Strict"
    ]  # fmt: skip
    assert client.post("/logout", content=b"", headers=headers).status_code == 204


def test_logout_needs_a_session_and_the_unsafe_checks(sa) -> None:
    cookie = _login(sa.anon(), sa.token).headers["set-cookie"].split(";")[0]
    client = sa.anon()  # a fresh client: no cookie jar, the cookie only where it is sent below
    ok = {**LOGIN_HEADERS, "Cookie": cookie}
    assert client.post("/logout", content=b"{}", headers=LOGIN_HEADERS).status_code == 401
    evil = {**ok, "Origin": "https://evil.example"}
    assert client.post("/logout", content=b"{}", headers=evil).status_code == 403
    plain = {**ok, "Content-Type": "text/plain"}
    assert client.post("/logout", content=b"{}", headers=plain).status_code == 415
    big = b'{"pad":"' + b"x" * 1100 + b'"}'
    assert client.post("/logout", content=big, headers=ok).status_code == 413
    assert client.post("/logout", content=b'{"a":1}', headers=ok).status_code == 400


# --- the login page -------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def test_the_login_page_is_self_contained_with_a_matching_csp(sa) -> None:
    res = sa.anon().get("/login")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    html = res.text
    assert not re.search(r"<link|\bsrc=|\bhref=|@import|url\(", html)
    css = re.search(r"<style>(.*?)</style>", html, re.S)
    js = re.search(r"<script>(.*?)</script>", html, re.S)
    assert css and js and html.count("<script") == 1 and html.count("<style") == 1
    csp = res.headers["content-security-policy"]
    assert csp == (
        f"default-src 'none'; style-src {_sha(css.group(1))}; script-src {_sha(js.group(1))}; "
        "connect-src 'self'; form-action 'none'; frame-ancestors 'none'"
    )
    assert "X-Spotdown-UI" in js.group(1) and "application/json" in js.group(1)


# --- no leaks -------------------------------------------------------------------------------------


def test_neither_the_token_nor_a_cookie_is_logged_or_returned(sa, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    client = sa.anon()
    bodies = []
    res = _login(client, sa.token)
    cookie = res.headers["set-cookie"].split(";")[0]
    value = cookie.split("=", 1)[1]
    bodies.append(res.text)
    for r in (
        _login(client, sa.token[:-1] + "0"),
        client.get("/ui.css"),
        client.get("/api/v1/tasks/x", headers={"Cookie": cookie, "X-Spotdown-UI": "1"}),
        client.get("/login"),
        client.post("/logout", content=b"{}", headers={**LOGIN_HEADERS, "Cookie": cookie}),
        client.get("/nope", headers={"Cookie": "spotdown_session=" + value[:-1] + "0"}),
    ):
        bodies.append(r.text)
    out = capsys.readouterr()
    for secret in (sa.token, value, value.split(".")[1]):
        assert secret not in caplog.text and secret not in out.out and secret not in out.err
        assert all(secret not in body for body in bodies)
