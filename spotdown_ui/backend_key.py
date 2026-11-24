"""The UI's caller key for the backend, read once from ``SPOTDOWN_BACKEND_KEY_FILE`` (ADR 0005
Decision 3 and 7). Without a readable, valid file the app does not start (fails closed).

The key is never printed, logged, put in a repr or sent anywhere but in the ``Authorization``
header of a request to ``SPOTDOWN_BACKEND_URL`` (``backend.BackendClient``). An error names the
setting and the rule, never the content.
"""

from __future__ import annotations

from pathlib import Path

MIN_KEY_LENGTH = 32  # the backend's rule for every caller key
_READ_LIMIT = 4096


class BackendKeyError(RuntimeError):
    """The key file is missing, unreadable, too short or not header-safe. Never carries content."""


class BackendKey:
    """The key, kept out of every repr and str, so a stray log line or traceback cannot show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if len(value) < MIN_KEY_LENGTH:
            raise BackendKeyError(
                f"SPOTDOWN_BACKEND_KEY_FILE must hold at least {MIN_KEY_LENGTH} characters"
            )
        # Printable ASCII without spaces: the one set that goes into a header unchanged.
        if any(not 0x21 <= ord(c) <= 0x7E for c in value):
            raise BackendKeyError(
                "SPOTDOWN_BACKEND_KEY_FILE must hold printable ASCII without spaces"
            )
        self._value = value

    def __repr__(self) -> str:
        return "BackendKey(<redacted>)"

    __str__ = __repr__

    def authorization(self) -> str:
        """The one place the key leaves this object: the backend request's header value."""
        return "Bearer " + self._value


def load_backend_key(path: Path) -> BackendKey:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_READ_LIMIT + 1)
    except OSError:
        raise BackendKeyError("SPOTDOWN_BACKEND_KEY_FILE cannot be read") from None
    if len(raw) > _READ_LIMIT:
        raise BackendKeyError("SPOTDOWN_BACKEND_KEY_FILE is too large to be a key")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BackendKeyError(
            "SPOTDOWN_BACKEND_KEY_FILE must hold printable ASCII without spaces"
        ) from None
    return BackendKey(text.strip())
