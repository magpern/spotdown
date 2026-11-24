"""``POST /v2/spotify/resolve`` (ADR 0005, Decision 4; spotdown#76 as revised): strict parsing to
(kind, id), Spotify's public pages on ``open.spotify.com`` only, no credentials, fixed errors.
Every fetch is a fake (``spotify_fakes``); the netguard stays on."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import httpx
import pytest

from app.service import spotify_page, spotify_policy
from app.service.spotify_policy import LinkError, parse_link

from .service_helpers import AUTH, client_for, service_settings
from .spotify_fakes import (
    ALBUM_ID,
    PLAYLIST_ID,
    TRACK_ID,
    FakeSpotify,
    embed_html,
    list_entity,
    sid,
    track_entity,
)

URL = "/v2/spotify/resolve"
FIELDS = {"spotify_id", "title", "artists", "album", "duration_ms", "isrc"}
SPOTIFY_ENV = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_CLIENT_SECRET_FILE")


@pytest.fixture(autouse=True)
def _no_spotify_env(monkeypatch) -> None:
    for name in SPOTIFY_ENV:
        monkeypatch.delenv(name, raising=False)


def _client(tmp_path: Path, fake: FakeSpotify):
    client = client_for(service_settings(tmp_path))
    client.app.state.spotify = fake.client()  # type: ignore[attr-defined]
    return client


def _resolve(client, url: str):
    return client.post(URL, headers=AUTH, json={"url": url})


def _only_open_spotify(fake: FakeSpotify, kind: str, ident: str) -> None:
    """Every request went to https://open.spotify.com, on a path built from (kind, id)."""
    allowed = {f"/embed/{kind}/{ident}", "/oembed"}
    for r in fake.requests:
        assert r.url.scheme == "https" and r.url.host == "open.spotify.com" and r.url.port is None
        assert r.url.path in allowed
        if r.url.path == "/oembed":
            assert dict(r.url.params) == {"url": f"https://open.spotify.com/{kind}/{ident}"}
        else:
            assert r.url.query == b""
        assert "authorization" not in r.headers and "cookie" not in r.headers


def test_a_track(tmp_path: Path) -> None:
    fake = FakeSpotify()
    res = _resolve(_client(tmp_path, fake), f"https://open.spotify.com/track/{TRACK_ID}?si=abc")
    assert res.status_code == 200
    assert res.json() == {
        "tracks": [
            {
                "spotify_id": TRACK_ID,
                "title": "Song 0",
                "artists": ["Artist 0", "Guest 0"],
                "album": None,
                "duration_ms": 180000,
                "isrc": None,
            }
        ],
        "listed": 1,
        "total": 1,
        "truncated": False,
    }
    assert [r.url.path for r in fake.requests] == [f"/embed/track/{TRACK_ID}"]
    _only_open_spotify(fake, "track", TRACK_ID)


def test_an_album(tmp_path: Path) -> None:
    fake = FakeSpotify()
    body = _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}").json()
    assert body["listed"] == 12 and body["total"] is None and body["truncated"] is False
    assert [t["spotify_id"] for t in body["tracks"]] == [sid(n) for n in range(12)]
    assert {t["album"] for t in body["tracks"]} == {"The Album"}
    assert body["tracks"][3]["artists"] == ["Artist 3", "Guest 3"]
    assert body["tracks"][3]["duration_ms"] == 180003
    assert all(set(t) == FIELDS for t in body["tracks"])
    _only_open_spotify(fake, "album", ALBUM_ID)


def test_a_playlist(tmp_path: Path) -> None:
    fake = FakeSpotify(playlist_rows=30, totalCount=30)
    res = _resolve(_client(tmp_path, fake), f"open.spotify.com/intl-de/playlist/{PLAYLIST_ID}")
    body = res.json()
    assert res.status_code == 200
    assert (body["listed"], body["total"], body["truncated"]) == (30, 30, False)
    assert {t["album"] for t in body["tracks"]} == {None}
    _only_open_spotify(fake, "playlist", PLAYLIST_ID)


def test_a_short_playlist_page_reports_the_shortfall(tmp_path: Path) -> None:
    fake = FakeSpotify(playlist_rows=100, totalCount=742)
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert len(body["tracks"]) == 100
    assert (body["listed"], body["total"], body["truncated"]) == (100, 742, True)


@pytest.mark.parametrize("rows,truncated", [(500, False), (501, True), (650, True)])
def test_at_most_500_tracks(tmp_path: Path, rows, truncated) -> None:
    fake = FakeSpotify(playlist_rows=rows)
    body = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}").json()
    assert len(body["tracks"]) == 500
    assert body["listed"] == rows and body["truncated"] is truncated


def test_missing_fields_are_null_or_empty_and_still_200(tmp_path: Path) -> None:
    fake = FakeSpotify()
    rows = [
        {"uri": f"spotify:track:{sid(1)}", "title": "Only a title"},
        {"uri": f"spotify:track:{sid(2)}", "title": "Bad types", "subtitle": 7, "duration": "x"},
        {"uri": f"spotify:episode:{sid(3)}", "title": "an episode", "entityType": "episode"},
        {"uri": "spotify:local:abc", "title": "a local file"},
        {"uri": f"spotify:track:{sid(4)}"},  # no title: not usable
        "not an object",
    ]
    fake.pages[f"/embed/playlist/{PLAYLIST_ID}"] = embed_html(
        {"type": "playlist", "trackList": rows}
    )
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    assert res.status_code == 200
    blank = {"artists": [], "album": None, "duration_ms": None, "isrc": None}
    assert res.json() == {
        "tracks": [
            {"spotify_id": sid(1), "title": "Only a title", **blank},
            {"spotify_id": sid(2), "title": "Bad types", **blank},
        ],
        "listed": 2,
        "total": None,
        "truncated": False,
    }


def test_the_entity_is_found_off_the_observed_path(tmp_path: Path) -> None:
    fake = FakeSpotify()
    entity = track_entity()
    del entity["artists"], entity["duration"]
    fake.pages[f"/embed/track/{TRACK_ID}"] = (
        b'<script type="application/json" id="__NEXT_DATA__">'
        + json.dumps({"props": {"other": [{"x": entity}]}}).encode()
        + b"</script>"
    )
    body = _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}").json()
    assert body["tracks"][0]["title"] == "Song 0"
    assert body["tracks"][0]["artists"] == [] and body["tracks"][0]["duration_ms"] is None


@pytest.mark.parametrize("embed", ["missing", "no_json", "status"])
def test_a_track_falls_back_to_oembed_for_its_title(tmp_path: Path, embed) -> None:
    fake = FakeSpotify()
    path = f"/embed/track/{TRACK_ID}"
    if embed == "missing":
        del fake.pages[path]
    elif embed == "no_json":
        fake.pages[path] = b"<html><body>no data here</body></html>"
    else:
        fake.override = lambda r: httpx.Response(500) if r.url.path == path else None
    body = _resolve(_client(tmp_path, fake), f"spotify:track:{TRACK_ID}").json()
    assert body["tracks"] == [
        {
            "spotify_id": TRACK_ID,
            "title": "Song from oEmbed",
            "artists": [],
            "album": None,
            "duration_ms": None,
            "isrc": None,
        }
    ]
    _only_open_spotify(fake, "track", TRACK_ID)


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
    f"https://open.spotify.com/embed/track/{TRACK_ID}",
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
    assert all("evil" not in str(r.url) for r in fake.requests)
    _only_open_spotify(fake, "track", TRACK_ID)


@pytest.mark.parametrize("target", ["https://evil.example/x", "/embed/track/elsewhere"])
def test_a_redirect_is_never_followed(tmp_path: Path, target) -> None:
    fake = FakeSpotify()
    fake.override = lambda r: httpx.Response(302, headers={"Location": target})
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    assert res.status_code == 502
    assert fake.hosts() == {"open.spotify.com"}
    assert len(fake.requests) == 1


def test_an_oversized_body_is_refused(tmp_path: Path) -> None:
    fake = FakeSpotify()
    big = embed_html(list_entity("playlist", 3)) + b" " * spotify_policy.MAX_BODY_BYTES
    fake.override = lambda r: httpx.Response(200, content=big)
    res = _resolve(_client(tmp_path, fake), f"spotify:playlist:{PLAYLIST_ID}")
    assert res.status_code == 502


def test_an_oversized_stream_without_a_length_is_refused(tmp_path: Path) -> None:
    fake = FakeSpotify()
    sent = []

    def chunks():
        for _ in range(spotify_policy.MAX_BODY_BYTES // 65536 + 10):
            sent.append(1)
            yield b"x" * 65536

    fake.override = lambda r: httpx.Response(200, content=chunks())
    res = _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}")
    assert res.status_code == 502
    assert len(sent) <= spotify_policy.MAX_BODY_BYTES // 65536 + 2  # stopped at the cap


def test_a_declared_oversized_body_is_refused_unread(tmp_path: Path) -> None:
    fake = FakeSpotify()
    read = []

    def chunks():
        read.append(1)
        yield b"x"

    size = str(spotify_policy.MAX_BODY_BYTES + 1)
    fake.override = lambda r: httpx.Response(
        200, headers={"Content-Length": size}, content=chunks()
    )
    assert _resolve(_client(tmp_path, fake), f"spotify:album:{ALBUM_ID}").status_code == 502
    assert read == []


def _upstream_fails(kind: str):
    def answer(request: httpx.Request):
        if kind == "status":
            return httpx.Response(500, text="upstream-secret-text")
        if kind == "timeout":
            raise httpx.ReadTimeout(f"upstream-secret-text {request.url}", request=request)
        if kind == "no_track":
            return httpx.Response(
                200, content=embed_html({"trackList": []}) + b"upstream-secret-text"
            )
        if kind == "not_json":
            return httpx.Response(200, text="upstream-secret-text <html>")
        if kind == "rate":
            return httpx.Response(429, headers={"Retry-After": "9"}, text="upstream-secret-text")
        return None

    return answer


@pytest.mark.parametrize("link", [f"spotify:playlist:{PLAYLIST_ID}", f"spotify:track:{TRACK_ID}"])
@pytest.mark.parametrize("kind", ["status", "timeout", "no_track", "not_json", "rate"])
def test_no_usable_track_is_a_fixed_502(tmp_path: Path, caplog, kind, link) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeSpotify()
    fake.override = _upstream_fails(kind)
    res = _resolve(_client(tmp_path, fake), link)
    assert res.status_code == 502
    assert res.json() == {
        "error": {"code": "provider_error", "message": "the Spotify request failed"}
    }
    for text in (res.text, caplog.text, str(res.headers)):
        assert "upstream-secret-text" not in text


def test_no_spotify_setting_is_read(tmp_path: Path, monkeypatch) -> None:
    """Spotify settings set to values that would show if sent: no request carries them."""
    settings = service_settings(
        tmp_path, spotify_client_id="id-must-not-leak", spotify_client_secret="secret-not-sent"
    )
    fake = FakeSpotify()
    client = client_for(settings)
    client.app.state.spotify = fake.client()  # type: ignore[attr-defined]
    assert _resolve(client, f"spotify:track:{TRACK_ID}").status_code == 200
    for r in fake.requests:
        text = str(r.url) + str(r.headers) + r.content.decode("latin-1")
        assert "must-not-leak" not in text and "not-sent" not in text
    _only_open_spotify(fake, "track", TRACK_ID)


def test_the_app_builds_its_client_without_spotify_settings(tmp_path: Path) -> None:
    client = client_for(service_settings(tmp_path))
    assert client.app.state.spotify is not None  # type: ignore[attr-defined]
    for name in ("spotify_client_secret_file",):
        assert not hasattr(service_settings(tmp_path), name)


def test_the_policy_builds_only_open_spotify_urls() -> None:
    assert spotify_policy.embed_url("album", ALBUM_ID) == (
        f"https://open.spotify.com/embed/album/{ALBUM_ID}"
    )
    for bad in (("track", "../evil"), ("artist", TRACK_ID), ("track", TRACK_ID + "/x")):
        with pytest.raises(LinkError):
            spotify_policy.embed_url(*bad)
        with pytest.raises(LinkError):
            spotify_policy.oembed_url(*bad)
    assert not spotify_policy.is_allowed_url("https://open.spotify.com.evil.example/x")
    assert not spotify_policy.is_allowed_url("https://u@open.spotify.com/x")
    assert not spotify_policy.is_allowed_url("http://open.spotify.com/x")
    assert str(LinkError()) == "not a Spotify track, album or playlist link"


@pytest.mark.parametrize("module", [spotify_policy, spotify_page])
def test_the_parsing_modules_import_no_io(module) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add(("." * node.level) + (node.module or "").split(".")[0])
    assert roots <= {"__future__", "re", "json", "typing", "urllib", ".spotify_policy"}
