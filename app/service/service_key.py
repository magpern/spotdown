"""The one shared service key: loaded from a secrets file, compared in constant time.

Every ``/v2`` request carries ``Authorization: Bearer <service key>``; a missing or wrong key is
401 with no detail. The key is never logged, printed or put in a repr.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Optional

# Starlette's own helper, the one its router matches on. It is private: an upgrade that moves it
# fails at import (loudly), never by opening the guard; tests pin its root_path behaviour.
from starlette._utils import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from .errors import error_response

MIN_KEY_LENGTH = 32
V2_PREFIX = "/v2"


class ServiceKeyError(RuntimeError):
    """The service key file is missing, unreadable or too short. Never carries its content."""


class ServiceKey:
    """Holds only the key's SHA-256, so a stray repr or traceback cannot show the key."""

    __slots__ = ("_digest",)

    def __init__(self, key: str) -> None:
        if len(key) < MIN_KEY_LENGTH:
            raise ServiceKeyError(f"the service key must be at least {MIN_KEY_LENGTH} characters")
        self._digest = hashlib.sha256(key.encode("utf-8")).digest()

    def __repr__(self) -> str:
        return "ServiceKey(<redacted>)"

    def matches(self, presented: str) -> bool:
        # Both sides are fixed-length digests, so neither the content nor the length of the key
        # leaks through timing; hmac.compare_digest is constant-time in their content.
        candidate = hashlib.sha256(presented.encode("utf-8")).digest()
        return hmac.compare_digest(candidate, self._digest)

    def matches_authorization(self, header: Optional[str]) -> bool:
        if not header:
            return False
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        token = token.strip()
        return bool(token) and self.matches(token)


def load_service_key(path: Optional[Path]) -> ServiceKey:
    if path is None:
        raise ServiceKeyError("service mode needs SERVICE_KEY_FILE")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ServiceKeyError("the service key file cannot be read") from None
    return ServiceKey(raw.strip())


def is_v2_path(path: str) -> bool:
    return path == V2_PREFIX or path.startswith(V2_PREFIX + "/")


class ServiceKeyMiddleware:
    """Refuses every ``/v2`` request without the key before routing, body parsing or 404s."""

    def __init__(self, app: ASGIApp, key: ServiceKey) -> None:
        self.app = app
        self.key = key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # The path the router matches (``root_path`` stripped), so a deployment behind a path
        # prefix (``uvicorn --root-path``) is guarded the same way.
        if scope["type"] == "http" and is_v2_path(get_route_path(scope)):
            values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
            header = values[0].decode("latin-1") if len(values) == 1 else None
            if not self.key.matches_authorization(header):
                response = error_response(401, "unauthorized", "unauthorized")
                response.headers["WWW-Authenticate"] = "Bearer"
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
