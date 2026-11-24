"""``POST /v2/spotify/resolve`` with the backend's Spotify credentials (spotdown#76): the Web API on
``accounts.spotify.com`` and ``api.spotify.com`` only. Plus the path choice: both settings give the
API, neither gives the public pages (and no API host is ever called), one alone stops the start-up.
Every Spotify call is a fake; the netguard stays on."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from app.main import create_app
from app.service import spotify_policy
from app.service.spotify_api_client import WebApi
from app.service.spotify_client import PublicPages, SpotifyConfigError, resolver_from_settings

from .service_helpers import AUTH, client_for, service_settings
from .spotify_fakes import (
    ALBUM_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    PLAYLIST_ID,
    TOKEN,
    TRACK_ID,
    FakeSpotify,
    FakeWebApi,
    sid,
)

URL = "/v2/spotify/resolve"
SPOTIFY_ENV = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_CLIENT_SECRET_FILE")


@pytest.fixture(autouse=True)
def _no_spotify_env(monkeypatch) -> None:
    for name in SPOTIFY_ENV:
        monkeypatch.delenv(name, raising=False)


def _client(tmp_path: Path, fake: FakeWebApi):
    client = client_for(service_settings(tmp_path))
    client.app.state.spotify = fake.client()  # type: ignore[attr-defined]
    return client


def _resolve(client, url: str):
    return client.post(URL, headers=AUTH, json={"url": url})


def _only_api(fake: FakeWebApi) -> None:
    for r in fake.requests:
        assert r.url.scheme == "https" and r.url.port is None
        assert r.url.host in spotify_policy.API_HOSTS
        assert "next-page-never-fetched" not in str(r.url)


def test_a_track_with_its_isrc(tmp_path: Path) -> None:
    fake = FakeWebApi()
    res = _resolve(_client(tmp_path, fake), f"https://open.spotify.com/track/{TRACK_ID}")
    assert res.status_code == 200
    assert res.json() == {
        "tracks": [
            {
                "spotify_id": TRACK_ID,
                "title": "Song 0",
                "artists": ["Artist 0"],
                "album": "Album",
                "duration_ms": 180000,
                "isrc": "SEXXX0000000",
            }
        ],
        "listed": 1,
        "total": 1,
        "truncated": False,
    }
    assert [r.url.path for r in fake.requests] == ["/api/token", f"/v1/tracks/{TRACK_ID}"]
    _only_api(fake)


def test_an_album_over_two_pages(tmp_path: Path) -> None:
    fake = FakeWebApi(album_size=60)
    body = _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}").json()
    assert (body["listed"], body["total"], body["truncated"]) == (60, 60, False)
    assert [t["spotify_id"] for t in body["tracks"]] == [sid(n) for n in range(60)]
    assert {t["album"] for t in body["tracks"]} == {"The Album"}
    assert all(t["isrc"] for t in body["tracks"])
    _only_api(fake)


def test_a_paged_playlist(tmp_path: Path) -> None:
    fake = FakeWebApi(playlist_size=230)
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert (body["listed"], body["total"], body["truncated"]) == (230, 230, False)
    offsets = [r.url.params["offset"] for r in fake.requests if "playlists" in r.url.path]
    assert offsets == ["0", "100", "200"]
    assert sum(r.url.path == "/api/token" for r in fake.requests) == 1  # the token is cached
    _only_api(fake)


@pytest.mark.parametrize("size,truncated", [(500, False), (501, True), (650, True)])
def test_a_playlist_over_500_tracks_is_truncated(tmp_path: Path, size, truncated) -> None:
    fake = FakeWebApi(playlist_size=size)
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert len(body["tracks"]) == 500 and body["total"] == size
    assert body["truncated"] is truncated


def test_rows_that_are_not_tracks_are_skipped(tmp_path: Path) -> None:
    fake = FakeWebApi()
    items = [
        {"track": None},
        {"track": {"id": None, "type": "track", "name": "local file"}},
        {"track": {"id": sid(1), "type": "episode", "name": "a podcast"}},
        {"track": {"id": sid(2), "name": "ok", "artists": [{"name": 7}, {"name": "A"}]}},
    ]
    fake.override = lambda r: (
        httpx.Response(200, json={"items": items, "next": None, "total": 4})
        if "playlists" in r.url.path
        else None
    )
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert body["tracks"] == [
        {
            "spotify_id": sid(2),
            "title": "ok",
            "artists": ["A"],
            "album": None,
            "duration_ms": None,
            "isrc": None,
        }
    ]
    assert body["listed"] == 1 and body["truncated"] is False


def test_an_expired_token_is_renewed_once(tmp_path: Path) -> None:
    fake = FakeWebApi()
    client = _client(tmp_path, fake)
    assert _resolve(client, f"spotify:track:{TRACK_ID}").status_code == 200
    fake.token = "fake-app-token-renewed"
    assert _resolve(client, f"spotify:track:{TRACK_ID}").status_code == 200
    assert sum(r.url.path == "/api/token" for r in fake.requests) == 2


def test_the_token_request_uses_basic_auth_on_the_fixed_host(tmp_path: Path) -> None:
    fake = FakeWebApi()
    _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}")
    token_call = fake.requests[0]
    assert str(token_call.url) == spotify_policy.TOKEN_URL and token_call.method == "POST"
    assert token_call.headers["authorization"].startswith("Basic ")
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in fake.requests[1:])


@pytest.mark.parametrize("target", ["https://evil.example/x", "https://api.spotify.com/v1/x"])
def test_a_redirect_is_never_followed(tmp_path: Path, target) -> None:
    fake = FakeWebApi()
    fake.override = lambda r: (
        httpx.Response(302, headers={"Location": target})
        if r.url.host == "api.spotify.com"
        else None
    )
    assert _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}").status_code == 502
    assert fake.hosts() == {"accounts.spotify.com", "api.spotify.com"}
    assert len(fake.requests) == 2


def test_an_oversized_api_body_is_refused(tmp_path: Path) -> None:
    fake = FakeWebApi()
    big = b'{"items": [], "pad": "' + b"x" * spotify_policy.MAX_BODY_BYTES + b'"}'
    fake.override = lambda r: (
        httpx.Response(200, content=big) if r.url.host == "api.spotify.com" else None
    )
    assert _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").status_code == 502


def _upstream_fails(kind: str):
    def answer(request: httpx.Request) -> Optional[httpx.Response]:
        api = request.url.host == "api.spotify.com"
        if kind == "token" and not api:
            return httpx.Response(400, text=f"upstream-secret-text {CLIENT_SECRET}")
        if kind == "status" and api:
            return httpx.Response(500, text=f"upstream-secret-text {TOKEN}")
        if kind == "timeout" and api:
            raise httpx.ReadTimeout(f"upstream-secret-text {request.url}", request=request)
        if kind == "not_json" and api:
            return httpx.Response(200, text="upstream-secret-text <html>")
        if kind == "not_paging" and api:
            return httpx.Response(200, json={"items": "upstream-secret-text"})
        if kind == "rate" and api:
            return httpx.Response(429, headers={"Retry-After": "9"}, text="upstream-secret-text")
        return None

    return answer


@pytest.mark.parametrize("kind", ["token", "status", "timeout", "not_json", "not_paging", "rate"])
def test_a_spotify_failure_is_a_fixed_502(tmp_path: Path, caplog, kind) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeWebApi()
    fake.override = _upstream_fails(kind)
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    assert res.status_code == 502
    assert res.json() == {
        "error": {"code": "provider_error", "message": "the Spotify request failed"}
    }
    for text in (res.text, caplog.text, str(res.headers)):
        assert "upstream-secret-text" not in text
        assert CLIENT_SECRET not in text and TOKEN not in text


class BothFakes:
    """One transport that answers for both paths and records every request."""

    def __init__(self) -> None:
        self.pages, self.api = FakeSpotify(), FakeWebApi()
        self.requests: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        fake: Any = self.pages if request.url.host == "open.spotify.com" else self.api
        return fake(request)

    def hosts(self) -> set:
        return {r.url.host for r in self.requests}


def _secret_file(
    tmp_path: Path, content: Optional[str] = CLIENT_SECRET + "\n", name: str = "spotify_secret"
) -> Path:
    path = tmp_path / name
    if content is not None:
        path.write_text(content, encoding="utf-8")
    return path


LINKS = [
    f"spotify:track:{TRACK_ID}",
    f"spotify:album:{ALBUM_ID}",
    f"spotify:playlist:{PLAYLIST_ID}",
]


@pytest.mark.parametrize("link", LINKS)
def test_without_credentials_no_api_host_is_called(link) -> None:
    both = BothFakes()
    resolver = resolver_from_settings("", None, httpx.MockTransport(both))
    assert isinstance(resolver, PublicPages)
    kind, ident = spotify_policy.parse_link(link)
    assert resolver.resolve(kind, ident)["tracks"]
    assert both.hosts() == {"open.spotify.com"}
    assert not both.hosts() & spotify_policy.API_HOSTS


@pytest.mark.parametrize("link", LINKS)
def test_with_credentials_only_the_api_hosts_are_called(tmp_path: Path, link) -> None:
    both = BothFakes()
    resolver = resolver_from_settings(CLIENT_ID, _secret_file(tmp_path), httpx.MockTransport(both))
    assert isinstance(resolver, WebApi)
    kind, ident = spotify_policy.parse_link(link)
    assert all(t["isrc"] for t in resolver.resolve(kind, ident)["tracks"])
    assert both.hosts() <= spotify_policy.API_HOSTS and "open.spotify.com" not in both.hosts()


def test_the_app_picks_the_path_from_its_settings(tmp_path: Path) -> None:
    plain = create_app(service_settings(tmp_path))
    assert isinstance(plain.state.spotify, PublicPages)
    keyed = create_app(
        service_settings(
            tmp_path, spotify_client_id=CLIENT_ID, spotify_client_secret_file=_secret_file(tmp_path)
        )
    )
    assert isinstance(keyed.state.spotify, WebApi)
    assert CLIENT_SECRET not in repr(keyed.state.spotify)
    assert not hasattr(keyed.state.spotify, "__dict__")


@pytest.mark.parametrize(
    "case", ["id_only", "id_and_env_secret", "file_only", "missing_file", "empty_file", "blank_id"]
)
def test_incomplete_spotify_settings_stop_the_start_up(tmp_path: Path, caplog, case) -> None:
    caplog.set_level(logging.DEBUG)
    overrides = {
        "id_only": {"spotify_client_id": CLIENT_ID},
        "id_and_env_secret": {
            "spotify_client_id": CLIENT_ID,
            "spotify_client_secret": CLIENT_SECRET,
        },
        "file_only": {"spotify_client_secret_file": _secret_file(tmp_path)},
        "missing_file": {
            "spotify_client_id": CLIENT_ID,
            "spotify_client_secret_file": tmp_path / "missing",
        },
        "empty_file": {
            "spotify_client_id": CLIENT_ID,
            "spotify_client_secret_file": _secret_file(tmp_path, "  \n", "empty_secret"),
        },
        "blank_id": {
            "spotify_client_id": "  ",
            "spotify_client_secret_file": _secret_file(tmp_path),
        },
    }[case]
    with pytest.raises(SpotifyConfigError) as caught:
        create_app(service_settings(tmp_path, **overrides))
    for text in (str(caught.value), repr(caught.value), caplog.text):
        assert CLIENT_SECRET not in text


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/v1/playlists/x/tracks?offset=100",
        "https://api.spotify.com/v1/me/tracks?offset=100",
        "https://api.spotify.com.evil.example/v1/playlists/x/tracks",
    ],
)
def test_a_next_url_from_a_response_is_never_requested(tmp_path: Path, next_url) -> None:
    """Pages are built from (kind, id), offset and limit; a ``next`` in a body is a flag only."""
    fake = FakeWebApi(playlist_size=230)
    plain = fake.__call__

    def with_next(request: httpx.Request) -> Optional[httpx.Response]:
        if "playlists" not in request.url.path:
            return None
        fake.override = None
        response = plain(request)
        fake.override = with_next
        doc = response.json()
        if doc.get("next"):
            doc["next"] = next_url
        return httpx.Response(200, json=doc)

    fake.override = with_next
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert body["listed"] == 230
    urls = [str(r.url) for r in fake.requests]
    assert next_url not in urls
    assert all(
        u == spotify_policy.TOKEN_URL
        or u.startswith(f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks?limit=100&")
        for u in urls
    )
    _only_api(fake)


def test_an_album_over_50_tracks_is_paged_to_the_cap(tmp_path: Path) -> None:
    fake = FakeWebApi(album_size=560)
    body = _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}").json()
    assert len(body["tracks"]) == 500
    assert (body["listed"] > 500, body["total"], body["truncated"]) == (True, 560, True)
    offsets = [r.url.params["offset"] for r in fake.requests if r.url.path.endswith("/tracks")]
    assert offsets == [str(n) for n in range(50, 550, 50)]
