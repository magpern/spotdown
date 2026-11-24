"""A fake Spotify Web API behind ``httpx.MockTransport``: no socket is opened, every request is
recorded. Ids are 22 base62 characters, like the real ones."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

from app.service.spotify_client import SpotifyClient

CLIENT_ID = "fake-client-id"
CLIENT_SECRET = "fake-client-secret-0123456789abcdef"
TOKEN = "fake-app-token-9f8e7d6c5b4a"
TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
ALBUM_ID = "1DFixLWuPkv3KT3TnV35m3"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


def sid(n: int) -> str:
    return f"t{n:021d}"


def track(n: int, album: Optional[str] = "Album") -> Dict[str, Any]:
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


class FakeSpotify:
    def __init__(self, playlist_size: int = 230, album_size: int = 60) -> None:
        self.playlist_size = playlist_size
        self.album_size = album_size
        self.requests: List[httpx.Request] = []
        self.override: Optional[Callable[[httpx.Request], Optional[httpx.Response]]] = None
        self.token = TOKEN

    def client(self) -> SpotifyClient:
        return SpotifyClient(CLIENT_ID, CLIENT_SECRET, transport=httpx.MockTransport(self))

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
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "bad token"})
        query = {k: int(v[0]) for k, v in parse_qs(url.query).items()}
        path = url.path
        if path == f"/v1/tracks/{TRACK_ID}":
            return httpx.Response(200, json={**track(0), "id": TRACK_ID})
        if path == f"/v1/albums/{ALBUM_ID}":
            page = self._page(self.album_size, 0, 50, lambda n: track(n, album=None))
            return httpx.Response(200, json={"name": "The Album", "tracks": page})
        if path == f"/v1/albums/{ALBUM_ID}/tracks":
            rows = self._page(self.album_size, query["offset"], 50, lambda n: track(n, None))
            return httpx.Response(200, json=rows)
        if path == f"/v1/playlists/{PLAYLIST_ID}/tracks":
            rows = self._page(
                self.playlist_size, query["offset"], query["limit"], lambda n: {"track": track(n)}
            )
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"error": "not found"})

    @staticmethod
    def _page(size: int, offset: int, limit: int, row: Callable[[int], Any]) -> Dict[str, Any]:
        end = min(size, offset + limit)
        more = end < size
        return {
            "items": [row(n) for n in range(offset, end)],
            "next": "https://api.spotify.com/v1/next-page-never-fetched" if more else None,
            "total": size,
        }
