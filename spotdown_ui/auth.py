"""The UI's authentication: a pure ASGI middleware installed at construction (ADR 0003 items 1,
3, 4 and 6, carried over unchanged by ADR 0005).

Every UI route needs a session, except exactly ``GET /login``, ``POST /login`` and
``GET /healthz`` (matched on the exact method and the exact ``scope["path"]``). The ``http``
order is: the ``Host`` allowlist, the token state, the exemptions and the session, the
unsafe-method checks (``Origin`` and the exact ``Content-Type``), then ``X-Spotdown-UI`` on
``/api/``. ``POST /login`` and ``POST /logout`` are answered here, never by a route.
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, FrozenSet, MutableMapping, Optional, Tuple

from . import login_page
from .exposure_policy import HostHeader, Origin, expected_origin, origin_matches, parse_host_header
from .session_policy import (
    LOGIN_BODY_MAX,
    SAFE_METHODS,
    UNSAFE_CONTENT_TYPES,
    LoginRateLimit,
    content_type_ok,
    cookie_name,
    cookie_values,
    make_session,
    session_valid,
    set_cookie_header,
    token_matches,
)
from .token_store import TokenSource

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

EXEMPT = frozenset({("GET", "/login"), ("POST", "/login"), ("GET", "/healthz")})
_now: Callable[[], float] = time.time  # a seam for the tests


def _header(scope: Scope, name: bytes) -> Optional[str]:
    """The value of a header sent exactly once; ``None`` if it is absent or repeated."""
    values = [v for k, v in scope.get("headers", []) if k.lower() == name]
    return values[0].decode("latin-1") if len(values) == 1 else None


def _cookie_header(scope: Scope) -> str:
    return "; ".join(v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"cookie")


async def _respond(
    send: Send, status: int, detail: Optional[str] = None, headers: Optional[list] = None
) -> None:
    body = json.dumps({"detail": detail}).encode() if detail is not None else b""
    out = [(b"cache-control", b"no-store")] + list(headers or [])
    if body:
        out.append((b"content-type", b"application/json"))
    out.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": out})
    await send({"type": "http.response.body", "body": body})


async def _too_many(send: Send, retry: int) -> None:
    await _respond(send, 429, "Too many failed logins", [(b"retry-after", str(retry).encode())])


async def _read_capped(scope: Scope, receive: Receive) -> tuple[int, bytes]:
    """``(0, body)``, or an error status: 413 over 1 KiB (by ``Content-Length`` before reading,
    and while reading), 400 for a malformed ``Content-Length`` or a disconnect."""
    length = _header(scope, b"content-length")
    if length is not None:
        if not (length.isascii() and length.isdigit()):  # isdigit alone accepts "²"
            return 400, b""
        if int(length) > LOGIN_BODY_MAX:
            return 413, b""
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return 400, b""
        body += message.get("body", b"")
        if len(body) > LOGIN_BODY_MAX:
            return 413, b""
        if not message.get("more_body", False):
            return 0, body


class UiAuth:
    def __init__(
        self,
        app: ASGIApp,
        *,
        tokens: TokenSource,
        allowed_hosts: FrozenSet[str],
        public_origins: Tuple[Origin, ...],
    ) -> None:
        self.app = app
        self.tokens = tokens
        self.allowed_hosts = allowed_hosts
        self.public_origins = public_origins
        # One scheme for every listed origin (parse_public_origins), so one cookie choice.
        self.https = bool(public_origins) and public_origins[0].scheme == "https"
        self.cookie = cookie_name(self.https)
        self.limit = LoginRateLimit()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
        elif kind == "http":
            await self._http(scope, receive, send)
        elif kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})  # before any accept
        else:
            raise RuntimeError(f"the UI auth refuses the ASGI scope type {kind!r}")

    def _session(self, scope: Scope, token: str) -> bool:
        now = _now()
        return any(
            session_valid(v, token, now) for v in cookie_values(_cookie_header(scope), self.cookie)
        )

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        method, path = scope["method"], scope["path"]
        raw_host = _header(scope, b"host")
        host = parse_host_header(raw_host) if raw_host is not None else None
        if host is None or host.host not in self.allowed_hosts:
            return await _respond(send, 403, "Host not allowed")
        token = self.tokens.current()
        if token is None:
            if (method, path) == ("GET", "/healthz"):
                return await self.app(scope, receive, send)
            return await _respond(send, 503, self.tokens.message())
        if (method, path) == ("GET", "/login"):
            return await self._login_page(send)
        if (method, path) == ("POST", "/login"):
            return await self._login(scope, receive, send, token, host)
        if (method, path) not in EXEMPT and not self._session(scope, token):
            accept = (_header(scope, b"accept") or "").lower()
            if (method, path) == ("GET", "/") and "text/html" in accept:
                return await _respond(send, 303, headers=[(b"location", b"/login")])
            return await _respond(send, 401, "Sign in at /login")
        if method not in SAFE_METHODS:
            status = self._unsafe_problem(scope, method, path, host)
            if status:
                return await _respond(send, status, "Request refused")
        if (path == "/api" or path.startswith("/api/")) and _header(scope, b"x-spotdown-ui") != "1":
            return await _respond(send, 403, "Missing X-Spotdown-UI")
        if (method, path) == ("POST", "/logout"):
            return await self._logout(scope, receive, send)
        await self.app(scope, receive, send)

    def _expected(self, host: HostHeader) -> Optional[Origin]:
        return expected_origin(self.public_origins, host)

    def _unsafe_problem(self, scope: Scope, method: str, path: str, host: HostHeader) -> int:
        if not origin_matches(_header(scope, b"origin"), self._expected(host)):
            return 403
        expected = UNSAFE_CONTENT_TYPES.get((method, path))
        if expected is None or not content_type_ok(_header(scope, b"content-type"), expected):
            return 415
        return 0

    async def _login_page(self, send: Send) -> None:
        body = login_page.BODY
        headers = login_page.HEADERS + [(b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _login(
        self, scope: Scope, receive: Receive, send: Send, token: str, host: HostHeader
    ) -> None:
        """Steps 3 to 9 of ``POST /login`` (``Host`` and the token state ran already)."""
        retry = self.limit.retry_after(_now())
        if retry is not None:  # the token is not evaluated and the attempt does not count
            return await _too_many(send, retry)
        if not origin_matches(_header(scope, b"origin"), self._expected(host)):
            return await _respond(send, 403, "Origin not allowed")
        if _header(scope, b"x-spotdown-ui") != "1":
            return await _respond(send, 403, "Missing X-Spotdown-UI")
        if not content_type_ok(_header(scope, b"content-type"), "application/json"):
            return await _respond(send, 415, "Content-Type must be application/json")
        status, body = await _read_capped(scope, receive)
        if status:
            return await _respond(send, status, "Bad request body")
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            data = None
        submitted = data.get("token") if isinstance(data, dict) else None
        if not isinstance(submitted, str):
            return await _respond(send, 400, 'Send {"token": "..."}')
        # The body was awaited since step 3, so other attempts may have closed the limit: it is
        # checked again, atomically with the comparison and the count (no await in between).
        retry, ok = self.limit.attempt(_now(), lambda: token_matches(submitted, token))
        if retry is not None:
            return await _too_many(send, retry)
        if not ok:
            return await _respond(send, 401, "Wrong token")
        cookie = set_cookie_header(make_session(token, _now()), self.https)
        await _respond(send, 204, headers=[(b"set-cookie", cookie.encode("ascii"))])

    async def _logout(self, scope: Scope, receive: Receive, send: Send) -> None:
        status, body = await _read_capped(scope, receive)
        if status:
            return await _respond(send, status, "Bad request body")
        if body.strip():
            try:
                data = json.loads(body)
            except (ValueError, RecursionError):
                data = None
            if data != {}:
                return await _respond(send, 400, "Send {} or an empty body")
        cookie = set_cookie_header("", self.https, max_age=0)
        await _respond(send, 204, headers=[(b"set-cookie", cookie.encode("ascii"))])
