"""Hostile Spotify bodies (spotdown#88): deep nesting is a fixed 502 at every parse entry point,
the ``__NEXT_DATA__`` scan is linear on a body near the cap, and no request carries ``.netrc``
credentials. Every fetch is faked (``httpx.MockTransport``); the netguard stays on."""

from __future__ import annotations

import base64
import logging
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from app.service import spotify_page, spotify_policy

from .service_helpers import AUTH, client_for, service_settings
from .spotify_fakes import (
    PLAYLIST_ID,
    TRACK_ID,
    FakeSpotify,
    FakeWebApi,
    embed_html,
    list_entity,
)

URL = "/v2/spotify/resolve"
SPOTIFY_ENV = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_CLIENT_SECRET_FILE")
SECRET_TEXT = b"upstream-secret-text"
PROVIDER_ERROR = {"error": {"code": "provider_error", "message": "the Spotify request failed"}}
# Deep enough for the JSON decoder's recursion limit, as arrays and as objects.
DEEP = [b"[" * 200_000, b'{"a":' * 200_000]
DEEP_IDS = ["arrays", "objects"]


@pytest.fixture(autouse=True)
def _no_spotify_env(monkeypatch) -> None:
    for name in SPOTIFY_ENV:
        monkeypatch.delenv(name, raising=False)


def _client(tmp_path: Path, fake):
    client = client_for(service_settings(tmp_path))
    client.app.state.spotify = fake.client()  # type: ignore[attr-defined]
    return client


def _resolve(client, link: str):
    return client.post(URL, headers=AUTH, json={"url": link})


def _assert_fixed_502(res, caplog) -> None:
    assert res.status_code == 502
    assert res.json() == PROVIDER_ERROR
    for text in (res.text, caplog.text, str(res.headers)):
        assert SECRET_TEXT.decode() not in text


def _deep_page(deep: bytes) -> bytes:
    return b'<html><script id="__NEXT_DATA__">' + deep + SECRET_TEXT + b"</script></html>"


# AC1: every parse entry point.


@pytest.mark.parametrize("deep", DEEP, ids=DEEP_IDS)
def test_parse_json_turns_deep_nesting_into_a_value_error(deep) -> None:
    with pytest.raises(ValueError):
        spotify_page.parse_json(deep)


@pytest.mark.parametrize("deep", DEEP, ids=DEEP_IDS)
def test_next_data_deep_nesting_is_a_fixed_502(tmp_path: Path, caplog, deep) -> None:
    caplog.set_level(logging.DEBUG)
    assert spotify_page.next_data(_deep_page(deep)) is None
    fake = FakeSpotify()
    fake.pages[f"/embed/playlist/{PLAYLIST_ID}"] = _deep_page(deep)
    _assert_fixed_502(_resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}"), caplog)


@pytest.mark.parametrize("deep", DEEP, ids=DEEP_IDS)
def test_oembed_deep_nesting_is_a_fixed_502(tmp_path: Path, caplog, deep) -> None:
    caplog.set_level(logging.DEBUG)
    assert spotify_page.oembed_track(deep, TRACK_ID) is None
    fake = FakeSpotify()
    del fake.pages[f"/embed/track/{TRACK_ID}"]  # the embed page fails: oEmbed is parsed
    fake.pages["/oembed"] = deep + SECRET_TEXT
    _assert_fixed_502(_resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}"), caplog)
    assert any(r.url.path == "/oembed" for r in fake.requests)


@pytest.mark.parametrize("deep", DEEP, ids=DEEP_IDS)
@pytest.mark.parametrize("host", ["accounts.spotify.com", "api.spotify.com"])
def test_web_api_deep_nesting_is_a_fixed_502(tmp_path: Path, caplog, deep, host) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeWebApi()

    def deep_body(request: httpx.Request):
        return httpx.Response(200, content=deep + SECRET_TEXT) if request.url.host == host else None

    fake.override = deep_body
    _assert_fixed_502(_resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}"), caplog)
    assert host in fake.hosts()


# AC2: a linear scan for the __NEXT_DATA__ script.


class _TooSlow(Exception):
    pass


@contextmanager
def _deadline(seconds: float) -> Iterator[None]:
    """Fails a scan that runs past ``seconds`` instead of letting a regression hang the suite."""

    def stop(*_: object) -> None:
        raise _TooSlow(f"over {seconds}s")

    previous = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _near_cap(unit: bytes, tail: bytes = b"") -> bytes:
    """``unit`` repeated, then ``tail``, padded with spaces to exactly the body cap."""
    body = unit * ((spotify_policy.MAX_BODY_BYTES - len(tail)) // len(unit)) + tail
    return body + b" " * (spotify_policy.MAX_BODY_BYTES - len(body))


HOSTILE = {
    "unclosed_script_tags": _near_cap(b"<script "),
    "unterminated_next_data": _near_cap(b'<script id="__NEXT_DATA__">'),
    "many_other_scripts": _near_cap(b"<script x>", b"<script>"),
    "id_without_boundary": _near_cap(b'<script xid="__NEXT_DATA__"'),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_the_next_data_scan_is_linear_near_the_cap(name) -> None:
    body = HOSTILE[name]
    assert len(body) == spotify_policy.MAX_BODY_BYTES
    start = time.perf_counter()
    with _deadline(5.0):
        found = spotify_page.next_data(body)
    assert time.perf_counter() - start < 1.0
    assert found is None


@pytest.mark.parametrize(
    "html,expected",
    [
        (b'<script id="__NEXT_DATA__" type="application/json">{"a":1}</script>', {"a": 1}),
        (b'<SCRIPT TYPE="x" ID="__next_data__">{"a":2}</SCRIPT>', {"a": 2}),
        (b'<script>{"a":0}</script><script data-x id="__NEXT_DATA__">{"a":3}</script>', {"a": 3}),
        (b'<script id="__NEXT_DATA__">{"a":4}</script><script>x</script>', {"a": 4}),
        (b'<scriptid="__NEXT_DATA__">{"a":5}</script>', None),  # no word boundary before id
        (b'<script xid="__NEXT_DATA__">{"a":6}</script>', None),
        (b'<script _id="__NEXT_DATA__">{"a":9}</script>', None),
        (b'<script \xe9id="__NEXT_DATA__">{"a":10}</script>', {"a": 10}),  # non-ASCII: a boundary
        (b'<script xid="__NEXT_DATA__" id="__NEXT_DATA__">{"a":11}</script>', {"a": 11}),
        (b'<script id="__NEXT_DATA__">{"a":7}', None),  # no closing tag
        (b'<script id="__NEXT_DATA__"', None),  # no end of the opening tag
        (b'<div id="__NEXT_DATA__">{"a":8}</div>', None),
    ],
)
def test_the_next_data_scan_keeps_the_former_matches(html, expected) -> None:
    assert spotify_page.next_data(html) == expected


def test_the_fixture_page_is_still_read() -> None:
    entity = list_entity("playlist", 3)
    assert (
        spotify_page.next_data(embed_html(entity))["props"]["pageProps"]["state"]["data"]["entity"]
        == entity
    )


# AC3: .netrc is never consulted.

NETRC_LOGIN, NETRC_PASSWORD = "netrc-login", "netrc-password-must-not-leave"


@pytest.fixture()
def _netrc(tmp_path: Path, monkeypatch) -> str:
    """A ``~/.netrc`` with credentials for every Spotify host, and the Basic value they would
    make."""
    home = tmp_path / "home"
    home.mkdir()
    netrc = home / ".netrc"
    machines = sorted(spotify_policy.PAGE_HOSTS | spotify_policy.API_HOSTS)
    netrc.write_text(
        "".join(f"machine {m} login {NETRC_LOGIN} password {NETRC_PASSWORD}\n" for m in machines),
        encoding="utf-8",
    )
    netrc.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NETRC", str(netrc))
    pair = f"{NETRC_LOGIN}:{NETRC_PASSWORD}".encode()
    return base64.b64encode(pair).decode()


def _no_netrc_credentials(requests, basic: str) -> None:
    assert requests
    for r in requests:
        sent = " ".join(f"{k}: {v}" for k, v in r.headers.items()) + str(r.url)
        assert basic not in sent and NETRC_LOGIN not in sent and NETRC_PASSWORD not in sent


@pytest.mark.parametrize("link", [f"spotify:track:{TRACK_ID}", f"spotify:playlist:{PLAYLIST_ID}"])
def test_the_public_pages_never_send_netrc_credentials(tmp_path: Path, _netrc, link) -> None:
    fake = FakeSpotify()
    del fake.pages[f"/embed/track/{TRACK_ID}"]  # the track reaches oEmbed as well
    res = _resolve(_client(tmp_path, fake), link)
    _no_netrc_credentials(fake.requests, _netrc)
    assert res.status_code == 200
    assert all("authorization" not in r.headers for r in fake.requests)


def test_the_web_api_never_sends_netrc_credentials(tmp_path: Path, _netrc) -> None:
    fake = FakeWebApi()
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    _no_netrc_credentials(fake.requests, _netrc)
    assert res.status_code == 200
    assert {"accounts.spotify.com", "api.spotify.com"} <= fake.hosts()
