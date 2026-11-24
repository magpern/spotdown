"""``POST /v2/spotify/resolve`` (ADR 0005, Decision 4): strict parsing to (kind, id), only the two
fixed Spotify hosts, fixed errors. Every Spotify call is a fake (``spotify_fakes``); the netguard
stays on."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.service import spotify_policy
from app.service.spotify_client import SpotifyConfigError
from app.service.spotify_policy import LinkError, parse_link

from .service_helpers import AUTH, client_for, service_settings
from .spotify_fakes import (
    ALBUM_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    PLAYLIST_ID,
    TOKEN,
    TRACK_ID,
    FakeSpotify,
    sid,
)

URL = "/v2/spotify/resolve"
FIELDS = {"spotify_id", "title", "artists", "album", "duration_ms", "isrc"}


def _client(tmp_path: Path, fake: FakeSpotify):
    client = client_for(service_settings(tmp_path))
    client.app.state.spotify = fake.client()  # type: ignore[attr-defined]
    return client


def _resolve(client, url: str):
    return client.post(URL, headers=AUTH, json={"url": url})


def test_a_track(tmp_path: Path) -> None:
    fake = FakeSpotify()
    res = _resolve(_client(tmp_path, fake), f"https://open.spotify.com/track/{TRACK_ID}?si=abc")
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
        "truncated": False,
    }
    assert fake.hosts() == {"accounts.spotify.com", "api.spotify.com"}


def test_an_album_over_two_pages(tmp_path: Path) -> None:
    fake = FakeSpotify(album_size=60)
    res = _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}")
    body = res.json()
    assert res.status_code == 200 and body["truncated"] is False
    assert [t["spotify_id"] for t in body["tracks"]] == [sid(n) for n in range(60)]
    assert {t["album"] for t in body["tracks"]} == {"The Album"}
    assert all(set(t) == FIELDS for t in body["tracks"])
    paths = [r.url.path for r in fake.requests]
    assert paths.count(f"/v1/albums/{ALBUM_ID}/tracks") == 1


def test_a_paged_playlist(tmp_path: Path) -> None:
    fake = FakeSpotify(playlist_size=230)
    res = _resolve(_client(tmp_path, fake), f"open.spotify.com/intl-de/playlist/{PLAYLIST_ID}")
    body = res.json()
    assert res.status_code == 200 and body["truncated"] is False
    assert [t["spotify_id"] for t in body["tracks"]] == [sid(n) for n in range(230)]
    offsets = [r.url.params["offset"] for r in fake.requests if "playlists" in r.url.path]
    assert offsets == ["0", "100", "200"]
    assert sum(r.url.path == "/api/token" for r in fake.requests) == 1  # the token is cached
    assert all("next-page-never-fetched" not in str(r.url) for r in fake.requests)


@pytest.mark.parametrize("size,truncated", [(500, False), (501, True), (650, True)])
def test_a_playlist_over_500_tracks_is_truncated(tmp_path: Path, size, truncated) -> None:
    fake = FakeSpotify(playlist_size=size)
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert len(body["tracks"]) == 500
    assert body["truncated"] is truncated


def test_playlist_rows_that_are_not_tracks_are_skipped(tmp_path: Path) -> None:
    fake = FakeSpotify()

    def rows(request: httpx.Request):
        if "playlists" not in request.url.path:
            return None
        items = [
            {"track": None},
            {"track": {"id": None, "type": "track", "name": "local file"}},
            {"track": {"id": sid(1), "type": "episode", "name": "a podcast"}},
            {"track": {"id": sid(2), "name": "ok", "artists": [{"name": 7}, {"name": "A"}]}},
        ]
        return httpx.Response(200, json={"items": items, "next": None})

    fake.override = rows
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert body == {
        "tracks": [
            {
                "spotify_id": sid(2),
                "title": "ok",
                "artists": ["A"],
                "album": None,
                "duration_ms": None,
                "isrc": None,
            }
        ],
        "truncated": False,
    }


HOSTILE = [
    f"https://evil.example/track/{TRACK_ID}",
    f"https://open.spotify.com.evil.example/track/{TRACK_ID}",
    f"https://open.spotify.com@evil.example/track/{TRACK_ID}",
    f"https://user:pw@open.spotify.com/track/{TRACK_ID}",
    f"https://evil.example@open.spotify.com/track/{TRACK_ID}",
    f"https://open.spotify.com:8443/track/{TRACK_ID}",
    f"https://open.spotify.com\\@evil.example/track/{TRACK_ID}",
    f"https://evil.example/?u=https://open.spotify.com/track/{TRACK_ID}",
    f"ftp://open.spotify.com/track/{TRACK_ID}",
    f"//evil.example/track/{TRACK_ID}",
    f"https://open.spotify.com/track/{TRACK_ID}x",
    f"https://open.spotify.com/track/{TRACK_ID[:-1]}",
    f"https://open.spotify.com/artist/{TRACK_ID}",
    f"https://open.spotify.com/track/{TRACK_ID}/../../evil",
    f"https://open.spotify.com/track/../track/{TRACK_ID}",
    f"https://open.spotify.com/ track/{TRACK_ID}",
    f"https://open.spotify.com/track/{TRACK_ID}\n.evil.example",
    f"spotify:track:{TRACK_ID}:evil",
    f"spotify:show:{TRACK_ID}",
    f"https://api.spotify.com/v1/tracks/{TRACK_ID}",
    "https://open.spotify.com/track/0000000000000000000%2F",
    "",
    " ",
]


@pytest.mark.parametrize("url", HOSTILE)
def test_a_hostile_url_is_refused_before_any_call(tmp_path: Path, url: str) -> None:
    fake = FakeSpotify()
    res = _resolve(_client(tmp_path, fake), url)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_request"
    assert "evil" not in res.text
    assert fake.requests == []


@pytest.mark.parametrize(
    "url",
    [
        f"https://open.spotify.com/track/{TRACK_ID}?si=x&u=https://evil.example/a",
        f"https://open.spotify.com/track/{TRACK_ID}?redirect=evil.example#evil.example",
        f"http://OPEN.SPOTIFY.COM/track/{TRACK_ID}/",
        f"  spotify:track:{TRACK_ID}  ",
    ],
)
def test_a_query_naming_another_host_is_reduced_to_kind_and_id(tmp_path: Path, url) -> None:
    assert parse_link(url) == ("track", TRACK_ID)
    fake = FakeSpotify()
    assert _resolve(_client(tmp_path, fake), url).status_code == 200
    assert fake.hosts() <= spotify_policy.ALLOWED_HOSTS
    assert all("evil" not in str(r.url) and r.url.scheme == "https" for r in fake.requests)


def test_a_redirect_is_never_followed(tmp_path: Path) -> None:
    fake = FakeSpotify()
    fake.override = lambda r: (
        httpx.Response(302, headers={"Location": "https://evil.example/x"})
        if r.url.host == "api.spotify.com"
        else None
    )
    res = _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}")
    assert res.status_code == 502
    assert fake.hosts() == {"accounts.spotify.com", "api.spotify.com"}


def test_the_policy_refuses_hosts_it_did_not_build() -> None:
    with pytest.raises(LinkError):
        spotify_policy.first_url("track", "../evil")
    with pytest.raises(LinkError):
        spotify_policy.page_url("track", PLAYLIST_ID, 0)
    assert str(LinkError()) == "not a Spotify track, album or playlist link"


@pytest.mark.parametrize(
    "overrides",
    [{}, {"spotify_client_id": CLIENT_ID}, {"spotify_client_secret": CLIENT_SECRET}, "file_only"],
)
def test_without_both_credentials_it_is_disabled(tmp_path: Path, overrides) -> None:
    if overrides == "file_only":
        secret = tmp_path / "spotify_secret"
        secret.write_text(CLIENT_SECRET, encoding="utf-8")
        overrides = {"spotify_client_secret_file": secret}
    client = client_for(service_settings(tmp_path, **overrides))
    res = _resolve(client, f"spotify:track:{TRACK_ID}")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "spotify_disabled"


def test_both_credentials_enable_it_and_the_secret_stays_hidden(tmp_path: Path) -> None:
    secret = tmp_path / "spotify_secret"
    secret.write_text(CLIENT_SECRET + "\n", encoding="utf-8")
    settings = service_settings(
        tmp_path, spotify_client_id=CLIENT_ID, spotify_client_secret_file=secret
    )
    app = create_app(settings)
    assert app.state.spotify is not None
    assert CLIENT_SECRET not in repr(app.state.spotify)
    assert not hasattr(app.state.spotify, "__dict__")


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_an_unusable_secret_file_stops_the_start_up(tmp_path: Path, content) -> None:
    secret = tmp_path / "spotify_secret"
    if content is not None:
        secret.write_text(content, encoding="utf-8")
    settings = service_settings(
        tmp_path, spotify_client_id=CLIENT_ID, spotify_client_secret_file=secret
    )
    with pytest.raises(SpotifyConfigError):
        create_app(settings)


def _upstream_fails(kind: str):
    def answer(request: httpx.Request):
        if kind == "token" and request.url.host == "accounts.spotify.com":
            return httpx.Response(400, text=f"upstream-secret-text {CLIENT_SECRET}")
        if kind == "status" and request.url.host == "api.spotify.com":
            return httpx.Response(500, text=f"upstream-secret-text {TOKEN}")
        if kind == "timeout" and request.url.host == "api.spotify.com":
            raise httpx.ReadTimeout(f"upstream-secret-text {request.url}", request=request)
        if kind == "not_json" and request.url.host == "api.spotify.com":
            return httpx.Response(200, text="upstream-secret-text <html>")
        if kind == "not_paging" and request.url.host == "api.spotify.com":
            return httpx.Response(200, json={"items": "upstream-secret-text"})
        if kind == "rate" and request.url.host == "api.spotify.com":
            return httpx.Response(429, headers={"Retry-After": "9"}, text="upstream-secret-text")
        return None

    return answer


@pytest.mark.parametrize("kind", ["token", "status", "timeout", "not_json", "not_paging", "rate"])
def test_a_spotify_failure_is_a_fixed_502(tmp_path: Path, caplog, kind) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeSpotify()
    fake.override = _upstream_fails(kind)
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    assert res.status_code == 502
    assert res.json() == {
        "error": {"code": "provider_error", "message": "the Spotify request failed"}
    }
    for text in (res.text, caplog.text, str(res.headers)):
        assert "upstream-secret-text" not in text
        assert CLIENT_SECRET not in text and TOKEN not in text


def test_an_expired_token_is_renewed_once(tmp_path: Path) -> None:
    fake = FakeSpotify()
    client = _client(tmp_path, fake)
    assert _resolve(client, f"spotify:track:{TRACK_ID}").status_code == 200
    fake.token = "fake-app-token-renewed"
    assert _resolve(client, f"spotify:track:{TRACK_ID}").status_code == 200
    assert sum(r.url.path == "/api/token" for r in fake.requests) == 2


def test_the_token_request_uses_basic_auth_on_the_fixed_host(tmp_path: Path) -> None:
    fake = FakeSpotify()
    _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}")
    token_call = fake.requests[0]
    assert str(token_call.url) == "https://accounts.spotify.com/api/token"
    assert token_call.method == "POST"
    assert token_call.headers["authorization"].startswith("Basic ")
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in fake.requests[1:])


def test_the_policy_module_imports_no_io() -> None:
    path = Path(spotify_policy.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"__future__", "re", "typing", "urllib"}
