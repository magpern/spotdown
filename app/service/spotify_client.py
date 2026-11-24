"""The fetches of ``POST /v2/spotify/resolve``: Spotify's public pages on ``open.spotify.com`` only
(spotdown#76 as revised). No credentials, no Web API.

Every URL comes from ``spotify_policy`` and is checked against the one host before it is sent.
No redirect is followed. A body over ``MAX_BODY_BYTES`` is refused (unread when its length is
declared). Every failure is one ``SpotifyFailure`` with a fixed reason: no upstream text reaches
an error or a log.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from . import spotify_page as page
from . import spotify_policy as policy

log = logging.getLogger(__name__)
TIMEOUT_S = 15.0


class SpotifyFailure(RuntimeError):
    """A Spotify page could not be used. Its message is a fixed reason, never upstream text."""


class SpotifyClient:
    __slots__ = ("_http",)

    def __init__(self, transport: Optional[httpx.BaseTransport] = None) -> None:
        # trust_env stays on: an operator's HTTPS proxy is honoured; no credential is ever sent.
        self._http = httpx.Client(transport=transport, follow_redirects=False, timeout=TIMEOUT_S)

    def resolve(self, kind: str, sid: str) -> Dict[str, Any]:
        """The resolve answer (``spotify_policy.answer``). Raises SpotifyFailure when no page
        gives a usable track."""
        tracks, total = [], None
        try:
            tracks, total = page.embed_tracks(self.fetch(policy.embed_url(kind, sid)), kind, sid)
        except SpotifyFailure:
            if kind != "track":
                raise
        if not tracks and kind == "track":
            track = page.oembed_track(self.fetch(policy.oembed_url(kind, sid)), sid)
            tracks = [track] if track is not None else []
        if not tracks:
            log.warning("spotify resolve failed: no usable track on the page")
            raise SpotifyFailure("no usable track")
        return policy.answer(tracks, total, kind)

    def fetch(self, url: str) -> bytes:
        if not policy.is_allowed_url(url):
            raise SpotifyFailure("refused host")  # unreachable with the built URLs; a guard
        try:
            with self._http.stream("GET", url) as response:
                if response.status_code != 200:  # a redirect too: it is never followed
                    log.warning("spotify page failed: status %d", response.status_code)
                    raise SpotifyFailure("status")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > policy.MAX_BODY_BYTES:
                    log.warning("spotify page refused: declared body over the cap")
                    raise SpotifyFailure("too large")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > policy.MAX_BODY_BYTES:
                        log.warning("spotify page refused: body over the cap")
                        raise SpotifyFailure("too large")
                return bytes(body)
        except httpx.HTTPError as exc:
            log.warning("spotify page failed: %s", type(exc).__name__)
            raise SpotifyFailure("transport") from None
