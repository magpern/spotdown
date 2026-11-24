"""The caller keys of ``/v2``: loaded from secrets files, compared in constant time.

``SERVICE_KEY_FILE`` is classbeat's key and is required; ``UI_SERVICE_KEY_FILE`` is the UI image's
and optional (ADR 0005, Decision 3). Both have the same rights. Every ``/v2`` request carries
``Authorization: Bearer <key>``; a missing or wrong key is 401 with no detail. A key is never
logged, printed or put in a repr.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Optional, Sequence

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
        return self.matches_digest(hashlib.sha256(presented.encode("utf-8")).digest())

    def matches_digest(self, candidate: bytes) -> bool:
        # Both sides are fixed-length digests, so neither the content nor the length of the key
        # leaks through timing; hmac.compare_digest is constant-time in their content.
        return hmac.compare_digest(candidate, self._digest)

    def same_key(self, other: "ServiceKey") -> bool:
        return other.matches_digest(self._digest)

    def matches_authorization(self, header: Optional[str]) -> bool:
        token = bearer_token(header)
        return token is not None and self.matches(token)


class ServiceKeys:
    """Every configured caller key. A request is compared with each of them, always all of them
    (no early exit on the first match), so the time taken does not tell which key matched."""

    __slots__ = ("_keys",)

    def __init__(self, keys: Sequence[ServiceKey]) -> None:
        if not keys:
            raise ServiceKeyError("at least one service key is required")
        for i, key in enumerate(keys):
            if any(key.same_key(other) for other in keys[:i]):
                raise ServiceKeyError("the caller keys must differ from each other")
        self._keys = tuple(keys)

    def __repr__(self) -> str:
        return f"ServiceKeys(<{len(self._keys)} redacted>)"

    def __len__(self) -> int:
        return len(self._keys)

    def matches(self, presented: str) -> bool:
        candidate = hashlib.sha256(presented.encode("utf-8")).digest()
        found = False
        for key in self._keys:
            found = key.matches_digest(candidate) | found  # `|`: no short circuit
        return found

    def matches_authorization(self, header: Optional[str]) -> bool:
        token = bearer_token(header)
        return token is not None and self.matches(token)


def bearer_token(header: Optional[str]) -> Optional[str]:
    """The token of ``Bearer <token>``, or None for any other header."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _read_key(path: Path, setting: str) -> ServiceKey:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ServiceKeyError(f"the {setting} file cannot be read") from None
    try:
        return ServiceKey(raw.strip())
    except ServiceKeyError:
        raise ServiceKeyError(
            f"the key in {setting} must be at least {MIN_KEY_LENGTH} characters"
        ) from None


def load_service_key(path: Optional[Path]) -> ServiceKey:
    if path is None:
        raise ServiceKeyError("the backend needs SERVICE_KEY_FILE")
    return _read_key(path, "SERVICE_KEY_FILE")


def load_service_keys(path: Optional[Path], ui_path: Optional[Path] = None) -> ServiceKeys:
    """classbeat's key (required) and, when ``UI_SERVICE_KEY_FILE`` is set, the UI's. Fails
    closed: a missing, unreadable or short file, or two equal keys, stops the start-up."""
    keys = [load_service_key(path)]
    if ui_path is not None:
        keys.append(_read_key(ui_path, "UI_SERVICE_KEY_FILE"))
    return ServiceKeys(keys)


def is_v2_path(path: str) -> bool:
    return path == V2_PREFIX or path.startswith(V2_PREFIX + "/")


class ServiceKeyMiddleware:
    """Refuses every ``/v2`` request without the key before routing, body parsing or 404s."""

    def __init__(self, app: ASGIApp, key: "ServiceKey | ServiceKeys") -> None:
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
