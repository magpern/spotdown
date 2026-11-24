"""Fakes of both resolve paths behind ``httpx.MockTransport``: no socket is opened, every request
is recorded (spotdown#76).

- ``FakeSpotify``: ``open.spotify.com``'s public pages. The embed pages are fixtures written to
  the observed shape of Spotify's public embed page (a Next.js ``__NEXT_DATA__`` script); that
  shape is an unverified assumption until the owner tries it live.
- ``FakeWebApi``: ``accounts.spotify.com`` and ``api.spotify.com``, to the Web API's documented
  shape."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

from app.service.spotify_api_client import WebApi
from app.service.spotify_client import PublicPages

CLIENT_ID = "fake-client-id"
CLIENT_SECRET = "fake-client-secret-0123456789abcdef"
TOKEN = "fake-app-token-9f8e7d6c5b4a"
TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
ALBUM_ID = "1DFixLWuPkv3KT3TnV35m3"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


# The first row of a real playlist embed page, as the owner-authorized live check returned it
# (2026-11-24): the artists are separated by a comma and a no-break space (U+00A0).
LIVE_ROW = {
    "uri": "spotify:track:4vTsKN15Kqo3Geo9r7FhAN",
    "title": "Movin' To The Sun",
    "subtitle": "HUGEL,\u00a0Ultra Nat\u00e9",
    "duration": 142200,
    "entityType": "track",
}


def sid(n: int) -> str:
    return f"t{n:021d}"


def row(n: int) -> Dict[str, Any]:
    return {
        "uri": f"spotify:track:{sid(n)}",
        "uid": f"uid{n}",
        "title": f"Song {n}",
        "subtitle": f"Artist {n},\u00a0Guest {n}",  # the live separator: comma, no-break space
        "isExplicit": False,
        "duration": 180000 + n,
        "entityType": "track",
    }


def embed_html(entity: Dict[str, Any]) -> bytes:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}, "page": "/embed"}
    return (
        "<!DOCTYPE html><html><head><title>Spotify</title></head><body><div id='__next'></div>"
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script></body></html>"
    ).encode()


def track_entity() -> Dict[str, Any]:
    return {
        "type": "track",
        "name": "Song 0",
        "uri": f"spotify:track:{TRACK_ID}",
        "id": TRACK_ID,
        "title": "Song 0",
        "artists": [{"name": "Artist 0", "uri": "spotify:artist:x"}, {"name": "Guest 0"}],
        "duration": 180000,
    }


def list_entity(kind: str, rows: int, **extra: Any) -> Dict[str, Any]:
    ident = ALBUM_ID if kind == "album" else PLAYLIST_ID
    return {
        "type": kind,
        "name": f"The {kind.title()}",
        "uri": f"spotify:{kind}:{ident}",
        "id": ident,
        "trackList": [row(n) for n in range(rows)],
        **extra,
    }


class FakeSpotify:
    def __init__(self, playlist_rows: int = 30, **playlist_extra: Any) -> None:
        # Bodies, served as a fresh 200 response each time.
        self.pages: Dict[str, bytes] = {
            f"/embed/track/{TRACK_ID}": embed_html(track_entity()),
            f"/embed/album/{ALBUM_ID}": embed_html(list_entity("album", 12)),
            f"/embed/playlist/{PLAYLIST_ID}": embed_html(
                list_entity("playlist", playlist_rows, **playlist_extra)
            ),
            "/oembed": json.dumps({"title": "Song from oEmbed", "type": "rich"}).encode(),
        }
        self.requests: List[httpx.Request] = []
        self.override: Optional[Callable[[httpx.Request], Optional[httpx.Response]]] = None

    def client(self) -> PublicPages:
        return PublicPages(transport=httpx.MockTransport(self))

    def hosts(self) -> set:
        return {r.url.host for r in self.requests}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            answer = self.override(request)
            if answer is not None:
                return answer
        if request.url.host != "open.spotify.com":
            return httpx.Response(599, text="the fake reached another host")
        page = self.pages.get(request.url.path)
        if page is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=page)


def api_track(n: int, album: Optional[str] = "Album") -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": sid(n),
        "type": "track",
        "name": f"Song {n}",
        "artists": [{"name": f"Artist {n}"}],
        "duration_ms": 180000 + n,
        "external_ids": {"isrc": f"SEXXX{n:07d}"},
    }
    if album is not None:
        item["album"] = {"name": album}
    return item


class FakeWebApi:
    def __init__(self, playlist_size: int = 230, album_size: int = 60) -> None:
        self.playlist_size = playlist_size
        self.album_size = album_size
        self.requests: List[httpx.Request] = []
        self.override: Optional[Callable[[httpx.Request], Optional[httpx.Response]]] = None
        self.token = TOKEN

    def client(self) -> WebApi:
        return WebApi(CLIENT_ID, CLIENT_SECRET, transport=httpx.MockTransport(self))

    def hosts(self) -> set:
        return {r.url.host for r in self.requests}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            answer = self.override(request)
            if answer is not None:
                return answer
        url = urlsplit(str(request.url))
        if url.netloc == "accounts.spotify.com" and url.path == "/api/token":
            return httpx.Response(200, json={"access_token": self.token, "expires_in": 3600})
        if url.netloc != "api.spotify.com":
            return httpx.Response(599, text="the fake reached another host")
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "bad token"})
        query = {k: int(v[0]) for k, v in parse_qs(url.query).items()}
        path = url.path
        if path == f"/v1/tracks/{TRACK_ID}":
            return httpx.Response(200, json={**api_track(0), "id": TRACK_ID})
        if path == f"/v1/albums/{ALBUM_ID}":
            page = self._page(self.album_size, 0, 50, lambda n: api_track(n, album=None))
            return httpx.Response(200, json={"name": "The Album", "tracks": page})
        if path == f"/v1/albums/{ALBUM_ID}/tracks":
            rows = self._page(self.album_size, query["offset"], 50, lambda n: api_track(n, None))
            return httpx.Response(200, json=rows)
        if path == f"/v1/playlists/{PLAYLIST_ID}/tracks":
            rows = self._page(
                self.playlist_size,
                query["offset"],
                query["limit"],
                lambda n: {"track": api_track(n)},
            )
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"error": "not found"})

    @staticmethod
    def _page(size: int, offset: int, limit: int, row: Callable[[int], Any]) -> Dict[str, Any]:
        end = min(size, offset + limit)
        return {
            "items": [row(n) for n in range(offset, end)],
            "next": "https://api.spotify.com/v1/next-page-never-fetched" if end < size else None,
            "total": size,
        }
