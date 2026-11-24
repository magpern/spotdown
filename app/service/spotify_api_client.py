"""The Web API path of ``POST /v2/spotify/resolve``, used only when the backend has
``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET_FILE`` (spotdown#76).

The client-credentials token and the paging walk follow ``app/spotify.py``, with these changes:
- the secret comes from the file only;
- only ``accounts.spotify.com`` and ``api.spotify.com`` are called;
- pages are read by offset, and Spotify's ``next`` URL is never fetched;
- every body is capped;
- every failure is a fixed ``SpotifyFailure``.

Neither the secret nor the token is in a repr, an error or a log.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import spotify_api as api
from . import spotify_policy as policy
from .spotify_client import Fetcher, SpotifyFailure
from .spotify_page import parse_json


class WebApi:
    __slots__ = ("_client_id", "_secret", "_fetch", "_token", "_expires", "_lock")

    def __init__(
        self, client_id: str, secret: str, transport: Optional[httpx.BaseTransport] = None
    ) -> None:
        self._client_id = client_id
        self._secret = secret
        self._fetch = Fetcher(policy.API_HOSTS, transport)
        self._token: Optional[str] = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "WebApi(<redacted>)"

    def resolve(self, kind: str, sid: str) -> Dict[str, Any]:
        if kind == "track":
            track = api.track_of(self._get(policy.api_first_url(kind, sid)))
            if track is None:
                raise SpotifyFailure("not a track")
            return policy.answer([track], 1, kind, more=False)
        tracks, total, more = self._album(sid) if kind == "album" else self._playlist(sid)
        if not tracks:
            raise SpotifyFailure("no usable track")
        return policy.answer(tracks, total, kind, more=more)

    def _album(self, sid: str) -> Tuple[List[Dict[str, Any]], Optional[int], bool]:
        album = self._get(policy.api_first_url("album", sid))
        if not isinstance(album, dict):
            raise SpotifyFailure("not an album")
        name = album.get("name") if isinstance(album.get("name"), str) else None
        rows, more, total = self._rows(album.get("tracks"))
        tracks = api.album_tracks(rows, name)
        return self._walk("album", sid, tracks, len(rows), more, total, name)

    def _playlist(self, sid: str) -> Tuple[List[Dict[str, Any]], Optional[int], bool]:
        rows, more, total = self._rows(self._get(policy.api_first_url("playlist", sid)))
        tracks = api.playlist_tracks(rows)
        return self._walk("playlist", sid, tracks, len(rows), more, total, None)

    def _walk(
        self,
        kind: str,
        sid: str,
        tracks: List[Dict[str, Any]],
        scanned: int,
        more: bool,
        total: Optional[int],
        album: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[int], bool]:
        """Reads further pages by offset until the cap, the scan bound or the last page."""
        while more and len(tracks) <= policy.MAX_TRACKS and scanned < policy.MAX_ROWS_SCANNED:
            rows, more, _ = self._rows(self._get(policy.api_page_url(kind, sid, scanned)))
            if not rows:
                break
            if kind == "album":
                tracks += api.album_tracks(rows, album)
            else:
                tracks += api.playlist_tracks(rows)
            scanned += len(rows)
        return tracks, total, more

    @staticmethod
    def _rows(page: Any) -> Tuple[List[Any], bool, Optional[int]]:
        try:
            return api.page_rows(page)
        except ValueError:
            raise SpotifyFailure("not a paging object") from None

    def _get(self, url: str) -> Any:
        status, body = self._fetch.fetch("GET", url, headers=self._auth())
        if status == 401:  # an expired token: one fresh token, one retry
            self._drop_token()
            status, body = self._fetch.fetch("GET", url, headers=self._auth())
        if status != 200:
            raise SpotifyFailure("status")
        return _json(body)

    def _auth(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._app_token()}"}

    def _app_token(self) -> str:
        with self._lock:
            if self._token is not None and self._expires > time.monotonic() + 30:
                return self._token
            status, body = self._fetch.fetch(
                "POST",
                policy.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._secret),
            )
            if status != 200:
                raise SpotifyFailure("token status")
            doc = _json(body)
            token = doc.get("access_token") if isinstance(doc, dict) else None
            if not isinstance(token, str) or not token:
                raise SpotifyFailure("no access token")
            lifetime = doc.get("expires_in", 3600)
            if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
                lifetime = 3600
            self._token, self._expires = token, time.monotonic() + lifetime
            return token

    def _drop_token(self) -> None:
        with self._lock:
            self._token, self._expires = None, 0.0


def _json(body: bytes) -> Any:
    try:
        return parse_json(body)
    except ValueError:
        raise SpotifyFailure("body is not JSON") from None
