"""The headers of ADR 0004 Decision 6 on every UI response (a pure ASGI middleware, outermost).

The strict Content-Security-Policy is added where a response has none (the login page brings its
own hash-based one, ADR 0003 item 4). ``X-Frame-Options``, ``X-Content-Type-Options`` and
``Referrer-Policy`` are always set; ``Cache-Control: no-store`` where none is set. No CORS header
is ever added.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, List, MutableMapping, Tuple

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
)
ALWAYS: List[Tuple[bytes, bytes]] = [
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]
DEFAULTS: List[Tuple[bytes, bytes]] = [
    (b"content-security-policy", CSP.encode("ascii")),
    (b"cache-control", b"no-store"),
]
_ALWAYS_NAMES = {name for name, _ in ALWAYS}


def with_headers(headers: List[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    out = [(k, v) for k, v in headers if k.lower() not in _ALWAYS_NAMES]
    present = {k.lower() for k, _ in out}
    out += ALWAYS
    out += [(k, v) for k, v in DEFAULTS if k not in present]
    return out


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = with_headers(list(message.get("headers", [])))
            await send(message)

        await self.app(scope, receive, send_with_headers)
