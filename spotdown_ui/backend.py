"""The UI's only outbound calls: fixed ``/v2`` routes on ``SPOTDOWN_BACKEND_URL`` (ADR 0005
Decision 7).

- One ``httpx.AsyncClient`` with the backend's origin as its base URL. Every request path is one
  of the fixed templates below, with an id that ``api_policy.checked_id`` passed; no request
  input chooses a host, a path prefix or a header.
- ``trust_env=False``: no ``HTTP(S)_PROXY``, ``NO_PROXY`` or ``.netrc`` from the environment can
  send a request (and the key) anywhere else. Redirects are never followed.
- The key goes only into the ``Authorization`` header of these requests. A failure is logged by
  its class name and the route's name, never with a URL, a header or a body.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx

from .api_policy import (
    BACKEND_DOWN,
    BACKEND_ERROR,
    BACKEND_TIMEOUT,
    SCOPE,
    Relayed,
    checked_id,
    relay,
)
from .backend_key import BackendKey
from .settings import Backend

log = logging.getLogger("spotdown_ui.backend")

JSON_ANSWER_MAX = 16 * 1024 * 1024  # an analysis document is well below this
SCOPE_QUERY = {"cache_scope": SCOPE}

# Name -> (method, path template). The only requests the UI ever makes.
ROUTES: Dict[str, Tuple[str, str]] = {
    "resolve": ("POST", "/v2/spotify/resolve"),
    "match": ("POST", "/v2/match"),
    "upload": ("PUT", "/v2/uploads"),
    "analyse": ("POST", "/v2/analyses"),
    "job": ("GET", "/v2/jobs/{id}"),
    "analysis": ("GET", "/v2/analyses/{id}"),
    "audio_job": ("POST", "/v2/audio"),
    "audio": ("GET", "/v2/audio/{id}"),
}


def idempotency_key() -> str:
    """A fresh random key for each create request (the backend's pattern)."""
    return "ui-" + secrets.token_hex(16)


class TooLarge(Exception):
    """A backend answer is over its cap."""


async def read_capped(response: httpx.Response, cap: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > cap:
            raise TooLarge()
    return bytes(body)


class BackendClient:
    def __init__(
        self,
        backend: Backend,
        key: BackendKey,
        timeout_s: float,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._key = key
        self._client = httpx.AsyncClient(
            base_url=backend.base_url,
            transport=transport,
            timeout=timeout_s,
            follow_redirects=False,
            trust_env=False,
        )

    def __repr__(self) -> str:
        return "BackendClient(<redacted>)"

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request(
        self,
        name: str,
        ident: Optional[str] = None,
        *,
        json: Optional[Dict[str, Any]] = None,
        content: Optional[AsyncIterator[bytes]] = None,
        scoped: bool = False,
        idempotent: bool = False,
    ) -> httpx.Request:
        method, template = ROUTES[name]
        path = template.format(id=checked_id(ident)) if ident is not None else template
        headers = {"Authorization": self._key.authorization(), "Accept": "application/json"}
        if idempotent:
            headers["Idempotency-Key"] = idempotency_key()
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        return self._client.build_request(
            method,
            path,
            json=json,
            content=content,
            params=SCOPE_QUERY if scoped else None,
            headers=headers,
        )

    async def _send_json(self, name: str, request: httpx.Request) -> Relayed:
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            log.warning("backend %s timed out (%s)", name, type(exc).__name__)
            return BACKEND_TIMEOUT
        except httpx.HTTPError as exc:
            log.warning("backend %s failed (%s)", name, type(exc).__name__)
            return BACKEND_DOWN
        try:
            raw = await read_capped(response, JSON_ANSWER_MAX)
        except TooLarge:
            log.warning("backend %s answer over the cap", name)
            return BACKEND_ERROR
        except httpx.HTTPError as exc:
            log.warning("backend %s answer broke off (%s)", name, type(exc).__name__)
            return BACKEND_DOWN
        finally:
            await response.aclose()
        return relay(response.status_code, response.headers.get("content-type"), raw)

    async def call(
        self,
        name: str,
        ident: Optional[str] = None,
        *,
        json: Optional[Dict[str, Any]] = None,
        scoped: bool = False,
        idempotent: bool = False,
    ) -> Relayed:
        request = self._request(name, ident, json=json, scoped=scoped, idempotent=idempotent)
        return await self._send_json(name, request)

    async def upload(self, chunks: AsyncIterator[bytes]) -> Relayed:
        """``PUT /v2/uploads`` in the UI's scope, the body streamed from ``chunks``."""
        request = self._request("upload", content=chunks, scoped=True, idempotent=True)
        return await self._send_json("upload", request)

    async def open_audio(self, audio_id: str) -> Tuple[Optional[httpx.Response], Relayed]:
        """``GET /v2/audio/{id}``: an open ``audio/mpeg`` response (the caller closes it), or
        ``None`` and the relayed error."""
        request = self._request("audio", audio_id)
        request.headers["Accept"] = "audio/mpeg"
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            log.warning("backend audio timed out (%s)", type(exc).__name__)
            return None, BACKEND_TIMEOUT
        except httpx.HTTPError as exc:
            log.warning("backend audio failed (%s)", type(exc).__name__)
            return None, BACKEND_DOWN
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if response.status_code == 200 and media == "audio/mpeg":
            return response, BACKEND_ERROR
        try:
            raw = await read_capped(response, JSON_ANSWER_MAX)
        except (TooLarge, httpx.HTTPError):
            raw = b""
        finally:
            await response.aclose()
        if 200 <= response.status_code < 300:
            return None, BACKEND_ERROR  # a success that is not an mp3 is not relayed
        return None, relay(response.status_code, response.headers.get("content-type"), raw)
