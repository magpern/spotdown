"""The UI image's settings, read from the environment (ADR 0005 Decision 7). Pure: the caller
passes the environment in; nothing here reads a file or opens a connection.

- ``SPOTDOWN_BACKEND_URL`` (required): ``http://`` or ``https://`` plus a host and an optional
  port, nothing else (no userinfo, path, query or fragment; one trailing ``/`` is allowed). It is
  the only host the UI ever calls.
- ``SPOTDOWN_BACKEND_KEY_FILE`` (required): the secret file with the UI's caller key
  (``backend_key`` reads it).
- ``UI_ACCESS_TOKEN_FILE`` (optional) or a token generated once in ``UI_DATA_DIR``; the ``Host``
  allowlist ``UI_ALLOWED_HOSTS`` and ``UI_PUBLIC_ORIGIN`` (ADR 0003).
- ``MAX_UPLOAD_BYTES`` and ``UI_UPLOAD_READ_S`` (ADR 0002's cap and read deadline),
  ``UI_MAX_AUDIO_BYTES`` (the largest mp3 relayed) and ``UI_BACKEND_TIMEOUT_S``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, FrozenSet, Mapping, NamedTuple, Optional, TypeVar

from .exposure_policy import (
    DEFAULT_ALLOWED_HOSTS,
    Origin,
    SettingsError,
    normalise_host,
    parse_allowed_hosts,
    parse_public_origin,
)

_BACKEND = re.compile(r"(https?)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?/?\Z")
_DEFAULT_PORTS = {"http": 80, "https": 443}

DEFAULT_DATA_DIR = "/data"
DEFAULT_MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # the backend's default cap (docs/contract-v2.md)
DEFAULT_UPLOAD_READ_S = 150.0  # ADR 0002's read deadline
DEFAULT_MAX_AUDIO_BYTES = 200 * 1024 * 1024
DEFAULT_BACKEND_TIMEOUT_S = 30.0

T = TypeVar("T", int, float)


class Backend(NamedTuple):
    """The one backend origin: scheme, canonical host and port (the default one when absent)."""

    scheme: str
    host: str
    port: int

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class UiSettings(NamedTuple):
    backend: Backend
    backend_key_file: Path
    data_dir: Path
    access_token_file: Optional[Path]
    allowed_hosts: FrozenSet[str]
    public_origin: Optional[Origin]
    max_upload_bytes: int
    upload_read_s: float
    max_audio_bytes: int
    backend_timeout_s: float


def parse_backend_url(value: str) -> Backend:
    """``SPOTDOWN_BACKEND_URL``; raises ``SettingsError`` (never echoing the value) otherwise."""
    match = _BACKEND.match(value)
    if not match:
        raise SettingsError(
            "SPOTDOWN_BACKEND_URL must be http:// or https:// plus a host and an optional port, "
            "with no user, path, query or fragment"
        )
    scheme, raw_host, port = match.group(1), match.group(2), match.group(3)
    host = normalise_host(raw_host)
    if host is None:
        raise SettingsError("SPOTDOWN_BACKEND_URL has an invalid host")
    number = int(port) if port else _DEFAULT_PORTS[scheme]
    if not 0 < number <= 65535:
        raise SettingsError("SPOTDOWN_BACKEND_URL has an invalid port")
    return Backend(scheme, host, number)


def _number(env: Mapping[str, str], name: str, default: T, kind: Callable[[str], T]) -> T:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a number") from None
    if not value > 0 or value != value:  # also refuses NaN
        raise SettingsError(f"{name} must be above 0")
    return value


def _path(env: Mapping[str, str], name: str) -> Optional[Path]:
    raw = env.get(name, "")
    return Path(raw) if raw else None


def parse_settings(env: Mapping[str, str]) -> UiSettings:
    """Every setting, checked; the first problem raises ``SettingsError``."""
    raw_backend = env.get("SPOTDOWN_BACKEND_URL", "")
    if not raw_backend:
        raise SettingsError("SPOTDOWN_BACKEND_URL is required")
    key_file = _path(env, "SPOTDOWN_BACKEND_KEY_FILE")
    if key_file is None:
        raise SettingsError("SPOTDOWN_BACKEND_KEY_FILE is required")
    return UiSettings(
        backend=parse_backend_url(raw_backend),
        backend_key_file=key_file,
        data_dir=Path(env.get("UI_DATA_DIR") or DEFAULT_DATA_DIR),
        access_token_file=_path(env, "UI_ACCESS_TOKEN_FILE"),
        allowed_hosts=parse_allowed_hosts(env.get("UI_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)),
        public_origin=parse_public_origin(env.get("UI_PUBLIC_ORIGIN", "")),
        max_upload_bytes=_number(env, "MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES, int),
        upload_read_s=_number(env, "UI_UPLOAD_READ_S", DEFAULT_UPLOAD_READ_S, float),
        max_audio_bytes=_number(env, "UI_MAX_AUDIO_BYTES", DEFAULT_MAX_AUDIO_BYTES, int),
        backend_timeout_s=_number(env, "UI_BACKEND_TIMEOUT_S", DEFAULT_BACKEND_TIMEOUT_S, float),
    )
