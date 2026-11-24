"""The fetches of ``POST /v2/spotify/resolve`` (spotdown#76) and the choice of path.

The path is the backend's setting, never the request's:
- ``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET_FILE`` both set: the Web API
  (``spotify_api_client``);
- neither set: Spotify's public pages on ``open.spotify.com`` (``PublicPages``);
- only one of them: the start-up stops.

Every URL comes from ``spotify_policy`` and is checked against the path's hosts before it is
sent. No redirect is followed. Every upstream body over ``MAX_BODY_BYTES`` is refused (unread
when its length is declared). Every failure is one ``SpotifyFailure`` with a fixed reason: no
upstream text, token or secret reaches an error or a log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple

import httpx

from . import spotify_page as page
from . import spotify_policy as policy

log = logging.getLogger(__name__)
TIMEOUT_S = 15.0


class SpotifyFailure(RuntimeError):
    """A Spotify answer could not be used. Its message is a fixed reason, never upstream text."""


class SpotifyConfigError(RuntimeError):
    """The Spotify settings are incomplete or the secret file is unusable. Never carries a
    secret."""


class Resolver(Protocol):
    def resolve(self, kind: str, sid: str) -> Dict[str, Any]: ...


class Fetcher:
    """GET or POST on a fixed set of hosts, no redirects, a capped body."""

    __slots__ = ("_http", "_hosts")

    def __init__(self, hosts: frozenset, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._hosts = hosts
        # trust_env stays on: an operator's HTTPS proxy is honoured.
        self._http = httpx.Client(transport=transport, follow_redirects=False, timeout=TIMEOUT_S)

    def __repr__(self) -> str:
        return f"Fetcher({sorted(self._hosts)})"

    def fetch(self, method: str, url: str, **kwargs: Any) -> Tuple[int, bytes]:
        """The status and, for a 200, the body. Any other status has no body read."""
        if not policy.is_allowed_url(url, self._hosts):
            raise SpotifyFailure("refused host")  # unreachable with the built URLs; a guard
        try:
            with self._http.stream(method, url, **kwargs) as response:
                if response.status_code != 200:  # a redirect too: it is never followed
                    log.warning("spotify call: status %d", response.status_code)
                    return response.status_code, b""
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > policy.MAX_BODY_BYTES:
                    log.warning("spotify call refused: declared body over the cap")
                    raise SpotifyFailure("too large")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > policy.MAX_BODY_BYTES:
                        log.warning("spotify call refused: body over the cap")
                        raise SpotifyFailure("too large")
                return 200, bytes(body)
        except httpx.HTTPError as exc:
            log.warning("spotify call failed: %s", type(exc).__name__)
            raise SpotifyFailure("transport") from None

    def get_ok(self, url: str) -> bytes:
        status, body = self.fetch("GET", url)
        if status != 200:
            raise SpotifyFailure("status")
        return body


class PublicPages:
    """Without credentials: the embed page, and oEmbed for a track the embed page does not give.
    Only ``open.spotify.com``; no credential is ever sent."""

    __slots__ = ("_fetch",)

    def __init__(self, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._fetch = Fetcher(policy.PAGE_HOSTS, transport)

    def __repr__(self) -> str:
        return "PublicPages()"

    def resolve(self, kind: str, sid: str) -> Dict[str, Any]:
        tracks, total = [], None
        try:
            html = self._fetch.get_ok(policy.embed_url(kind, sid))
            tracks, total = page.embed_tracks(html, kind, sid)
        except SpotifyFailure:
            if kind != "track":
                raise
        if not tracks and kind == "track":
            track = page.oembed_track(self._fetch.get_ok(policy.oembed_url(kind, sid)), sid)
            tracks = [track] if track is not None else []
        if not tracks:
            log.warning("spotify resolve failed: no usable track on the page")
            raise SpotifyFailure("no usable track")
        return policy.answer(tracks, total, kind)


def load_client_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise SpotifyConfigError("the SPOTIFY_CLIENT_SECRET_FILE file cannot be read") from None
    if not secret:
        raise SpotifyConfigError("the SPOTIFY_CLIENT_SECRET_FILE file is empty")
    return secret


def resolver_from_settings(
    client_id: str, secret_file: Optional[Path], transport: Optional[httpx.BaseTransport] = None
) -> Resolver:
    """The Web API with both settings, the public pages with neither; one alone fails closed."""
    client_id = client_id.strip()
    if not client_id and secret_file is None:
        return PublicPages(transport)
    if not client_id or secret_file is None:
        raise SpotifyConfigError(
            "set both SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET_FILE, or neither"
        )
    from .spotify_api_client import WebApi

    return WebApi(client_id, load_client_secret(secret_file), transport)
