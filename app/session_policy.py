"""Pure rules for the standalone session (ADR 0003 items 2, 4 and 6).

The token rules, the session cookie (value, MAC and attributes), the exact request content types
and the global login rate limit. No I/O: the caller passes the time in.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import deque
from typing import Callable, Deque, Optional

SESSION_TTL_S = 43200  # 12 h
LOGIN_BODY_MAX = 1024  # 1 KiB, for /login and /logout
MAX_SUBMITTED_TOKEN = 512
OWNER_TOKEN_MIN, OWNER_TOKEN_MAX = 32, 512
RATE_LIMIT_FAILURES = 5
RATE_LIMIT_WINDOW_S = 60.0

_GENERATED = re.compile(rb"[0-9a-f]{64}\n?\Z")
_COOKIE_VALUE = re.compile(r"([0-9]{1,12})\.([0-9a-f]{64})\Z")
_MAC_CONTEXT = b"spotdown-session-v1"

JSON = "application/json"
OCTET_STREAM = "application/octet-stream"
# The only unsafe requests that are let through, each with its one exact content type. Every
# other unsafe request gets 415 (default deny), so a later route cannot ship with a CORS-simple
# type by omission. spotdown#49 adds its upload route here with OCTET_STREAM.
UNSAFE_CONTENT_TYPES = {
    ("POST", "/api/v1/convert"): JSON,
    ("POST", "/login"): JSON,
    ("POST", "/logout"): JSON,
}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def parse_generated_token(raw: bytes) -> Optional[str]:
    """A generated token file: exactly 64 lowercase hex characters and one optional newline."""
    if not _GENERATED.match(raw):
        return None
    return raw[:64].decode("ascii")


def owner_token_problem(raw: bytes) -> tuple[Optional[str], str]:
    """``(token, "")`` for an owner-supplied file that satisfies the owner-token rule, else
    ``(None, rule)`` naming the failed rule (``"length"`` or ``"character set"``), never the
    token. Trailing whitespace is stripped first."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "character set"
    token = text.rstrip()
    if any(not (0x21 <= ord(c) <= 0x7E) or c in '"\\' for c in token):
        return None, "character set"
    if not OWNER_TOKEN_MIN <= len(token) <= OWNER_TOKEN_MAX:
        return None, "length"
    return token, ""


def _session_mac(token: str, expiry: str) -> str:
    key = hmac.new(token.encode("utf-8"), _MAC_CONTEXT, hashlib.sha256).digest()
    return hmac.new(key, expiry.encode("ascii"), hashlib.sha256).hexdigest()


def make_session(token: str, now: float) -> str:
    expiry = str(int(now) + SESSION_TTL_S)
    return f"{expiry}.{_session_mac(token, expiry)}"


def session_valid(value: str, token: str, now: float) -> bool:
    """``<expiry>.<mac>``: the shape, a MAC compared with ``compare_digest``, and an expiry in
    the future and at most 12 h ahead."""
    match = _COOKIE_VALUE.match(value)
    if not match:
        return False
    expiry, mac = match.group(1), match.group(2)
    if not hmac.compare_digest(mac.encode("ascii"), _session_mac(token, expiry).encode("ascii")):
        return False
    return now < int(expiry) <= now + SESSION_TTL_S


def token_matches(submitted: str, token: str) -> bool:
    """Compared as UTF-8 bytes (``surrogatepass``: JSON can carry a lone surrogate, which must
    be a wrong token, not an error). A submitted token over 512 characters never matches."""
    if len(submitted) > MAX_SUBMITTED_TOKEN:
        return False
    return hmac.compare_digest(
        submitted.encode("utf-8", "surrogatepass"), token.encode("utf-8", "surrogatepass")
    )


def cookie_name(https: bool) -> str:
    return "__Host-spotdown_session" if https else "spotdown_session"


def set_cookie_header(value: str, https: bool, max_age: int = SESSION_TTL_S) -> str:
    """The ``Set-Cookie`` value: ``HttpOnly``, ``SameSite=Strict``, ``Path=/``, never a
    ``Domain``; ``Secure`` (and the ``__Host-`` name) when the expected origin is https."""
    parts = [f"{cookie_name(https)}={value}", f"Max-Age={max_age}", "Path=/"]
    if https:
        parts.append("Secure")
    parts += ["HttpOnly", "SameSite=Strict"]
    return "; ".join(parts)


def cookie_values(header: str, name: str) -> list[str]:
    """Every value of the cookie ``name`` in a ``Cookie`` header."""
    out = []
    for part in header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            out.append(value)
    return out


def content_type_ok(header: Optional[str], expected: str) -> bool:
    """Exact media type, with only an optional ``charset=utf-8`` parameter."""
    if header is None:
        return False
    parts = [p.strip().lower() for p in header.split(";")]
    if parts[0] != expected:
        return False
    return parts[1:] in ([], ["charset=utf-8"])


class LoginRateLimit:
    """One global limit (no peer is trusted): five counted failures within 60 s close it for the
    next 60 s. While closed, attempts are refused and do not count."""

    def __init__(self) -> None:
        self._failures: Deque[float] = deque()
        self._closed_until = 0.0

    def retry_after(self, now: float) -> Optional[int]:
        """Seconds until the limit opens again, or ``None`` while it is open."""
        if now < self._closed_until:
            return max(1, math.ceil(self._closed_until - now))
        return None

    def record_failure(self, now: float) -> None:
        while self._failures and self._failures[0] <= now - RATE_LIMIT_WINDOW_S:
            self._failures.popleft()
        self._failures.append(now)
        if len(self._failures) >= RATE_LIMIT_FAILURES:
            self._failures.clear()
            self._closed_until = now + RATE_LIMIT_WINDOW_S

    def attempt(self, now: float, matches: Callable[[], bool]) -> tuple[Optional[int], bool]:
        """Step 9 as one synchronous step: ``(retry_after, False)`` while closed, without calling
        ``matches``; else ``(None, matches())``, recording a failure when it is false. The caller
        must not await between reading the body and calling this, and this never awaits, so
        concurrent attempts cannot all pass an open limit together."""
        retry = self.retry_after(now)
        if retry is not None:
            return retry, False
        ok = matches()
        if not ok:
            self.record_failure(now)
        return None, ok
