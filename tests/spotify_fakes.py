"""A fake ``open.spotify.com`` behind ``httpx.MockTransport``: no socket is opened, every request
is recorded. The embed pages are fixtures written to the observed shape of Spotify's public embed
page (a Next.js ``__NEXT_DATA__`` script); that shape is an unverified assumption until the owner
tries it live (spotdown#76)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.service.spotify_client import SpotifyClient

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
ALBUM_ID = "1DFixLWuPkv3KT3TnV35m3"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


def sid(n: int) -> str:
    return f"t{n:021d}"


def row(n: int) -> Dict[str, Any]:
    return {
        "uri": f"spotify:track:{sid(n)}",
        "uid": f"uid{n}",
        "title": f"Song {n}",
        "subtitle": f"Artist {n}, Guest {n}",
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

    def client(self) -> SpotifyClient:
        return SpotifyClient(transport=httpx.MockTransport(self))

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
