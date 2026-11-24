"""The Spotify Web API calls of ``POST /v2/spotify/resolve`` (ADR 0005, Decision 4).

Client credentials only, on two fixed hosts (``spotify_policy.ALLOWED_HOSTS``). Redirects are not
followed, every URL is checked against the fixed hosts before it is sent, and any failure is one
``SpotifyFailure`` with a fixed reason: no upstream body, token or secret reaches an error or a
log.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from . import spotify_policy as policy

log = logging.getLogger(__name__)
TIMEOUT_S = 15.0


class SpotifyFailure(RuntimeError):
    """A Spotify call failed. Its message is a fixed reason, never upstream text."""


class SpotifyConfigError(RuntimeError):
    """SPOTIFY_CLIENT_SECRET_FILE is set but cannot be used. Never carries its content."""


def load_client_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise SpotifyConfigError("the SPOTIFY_CLIENT_SECRET_FILE file cannot be read") from None
    if not secret:
        raise SpotifyConfigError("the SPOTIFY_CLIENT_SECRET_FILE file is empty")
    return secret


class SpotifyClient:
    """Holds the client id and secret and a cached app token; none of them is in its repr."""

    __slots__ = ("_client_id", "_secret", "_http", "_token", "_expires", "_lock")

    def __init__(
        self,
        client_id: str,
        secret: str,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._client_id = client_id
        self._secret = secret
        self._http = httpx.Client(transport=transport, follow_redirects=False, timeout=TIMEOUT_S)
        self._token: Optional[str] = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "SpotifyClient(<redacted>)"

    def resolve(self, kind: str, sid: str) -> Tuple[List[Dict[str, Any]], bool]:
        """The tracks of a parsed link (at most ``MAX_TRACKS``) and whether some were left out."""
        if kind == "track":
            track = policy.track_of(self._get(policy.first_url(kind, sid)))
            if track is None:
                raise SpotifyFailure("not a track")
            return [track], False
        if kind == "album":
            return self._album(sid)
        return self._playlist(sid)

    def _album(self, sid: str) -> Tuple[List[Dict[str, Any]], bool]:
        album = self._get(policy.first_url("album", sid))
        if not isinstance(album, dict):
            raise SpotifyFailure("not an album")
        name = album.get("name") if isinstance(album.get("name"), str) else None
        rows, more = self._rows(album.get("tracks"))
        tracks = policy.album_tracks(rows, name)
        scanned = len(rows)
        while more and len(tracks) <= policy.MAX_TRACKS and scanned < policy.MAX_ROWS_SCANNED:
            rows, more = self._rows(self._get(policy.page_url("album", sid, scanned)))
            if not rows:
                break
            tracks += policy.album_tracks(rows, name)
            scanned += len(rows)
        return policy.cap(tracks, more)

    def _playlist(self, sid: str) -> Tuple[List[Dict[str, Any]], bool]:
        tracks: List[Dict[str, Any]] = []
        scanned, more = 0, True
        while more and len(tracks) <= policy.MAX_TRACKS and scanned < policy.MAX_ROWS_SCANNED:
            rows, more = self._rows(self._get(policy.page_url("playlist", sid, scanned)))
            if not rows:
                break
            tracks += policy.playlist_tracks(rows)
            scanned += len(rows)
        return policy.cap(tracks, more)

    @staticmethod
    def _rows(page: Any) -> Tuple[List[Any], bool]:
        try:
            return policy.page_rows(page)
        except ValueError:
            raise SpotifyFailure("not a paging object") from None

    def _get(self, url: str) -> Any:
        response = self._send("GET", url, headers={"Authorization": f"Bearer {self._app_token()}"})
        if response.status_code == 401:  # an expired token: one fresh token, one retry
            self._drop_token()
            token = self._app_token()
            response = self._send("GET", url, headers={"Authorization": f"Bearer {token}"})
        return self._json(response)

    def _app_token(self) -> str:
        with self._lock:
            if self._token is not None and self._expires > time.monotonic() + 30:
                return self._token
            response = self._send(
                "POST",
                policy.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._secret),
            )
            body = self._json(response)
            token = body.get("access_token") if isinstance(body, dict) else None
            if not isinstance(token, str) or not token:
                raise SpotifyFailure("no access token")
            lifetime = body.get("expires_in", 3600)
            if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
                lifetime = 3600
            self._token, self._expires = token, time.monotonic() + lifetime
            return token

    def _drop_token(self) -> None:
        with self._lock:
            self._token, self._expires = None, 0.0

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in policy.ALLOWED_HOSTS:
            raise SpotifyFailure("refused host")  # unreachable with the fixed URLs; a guard
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("spotify call failed: %s", type(exc).__name__)
            raise SpotifyFailure("transport") from None

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if response.status_code != 200:  # a redirect too: it is never followed
            log.warning("spotify call failed: status %d", response.status_code)
            raise SpotifyFailure("status")
        try:
            return response.json()
        except ValueError:
            log.warning("spotify call failed: body is not JSON")
            raise SpotifyFailure("body") from None
