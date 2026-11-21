"""Pure rules for the standalone app's exposure checks (ADR 0003 items 5 and 6).

Parses and validates the ``ui_allowed_hosts`` and ``ui_public_origin`` settings, a request's
``Host`` header and its ``Origin`` header, and compares origins by the one rule ADR 0002 shares.
No I/O.
"""

from __future__ import annotations

import ipaddress
import re
from typing import FrozenSet, NamedTuple, Optional

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PORT = r"[0-9]{1,5}"
# A Host header: a name or IPv4 literal, or a bracketed IPv6 literal, and an optional port.
_HOST_HEADER = re.compile(rf"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::({_PORT}))?\Z")
# An exact origin: scheme, host and optional port; nothing else (no userinfo, path, query or
# fragment, not even a trailing "/").
_ORIGIN = re.compile(
    rf"([A-Za-z][A-Za-z0-9+.-]*)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::({_PORT}))?\Z"
)
_DEFAULT_PORTS = {"http": "80", "https": "443"}

DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,[::1]"


class Origin(NamedTuple):
    """An origin split by the one comparison rule: scheme and host lowercased (and nothing
    else), port as written (empty when absent). No default port is added or removed."""

    scheme: str
    host: str
    port: str


class SettingsError(ValueError):
    """A standalone exposure setting is invalid (raised at construction)."""


def _is_ipv4(text: str) -> bool:
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}", text):
        return False
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True


def _is_dns_name(text: str) -> bool:
    if not text or len(text) > 253:
        return False
    labels = text.split(".")
    if all(label.isdigit() for label in labels):
        return False  # all-numeric dotted text is an IPv4 literal or nothing
    return all(_LABEL.match(label) for label in labels)


def normalise_host(text: str) -> Optional[str]:
    """The canonical host for ``text`` (a DNS name, an IPv4 literal or a bracketed IPv6 literal),
    lowercased, IPv6 compressed and bracketed; ``None`` if it is none of these."""
    if text.startswith("[") and text.endswith("]"):
        try:
            return "[" + ipaddress.IPv6Address(text[1:-1]).compressed + "]"
        except ValueError:
            return None
    low = text.lower()
    if _is_ipv4(low) or _is_dns_name(low):
        return low
    return None


def parse_allowed_hosts(value: str) -> FrozenSet[str]:
    """``ui_allowed_hosts``: comma-separated DNS names (RFC 1123 labels), IPv4 literals or
    bracketed IPv6 literals. An empty entry, ``*``, a port, a scheme, a path or whitespace
    raises."""
    hosts = set()
    for entry in value.split(","):
        if not entry or entry != entry.strip() or re.search(r"\s", entry):
            raise SettingsError("ui_allowed_hosts: an entry is empty or has whitespace")
        host = normalise_host(entry)
        if host is None:
            raise SettingsError(
                "ui_allowed_hosts: each entry must be a DNS name, an IPv4 literal or a bracketed "
                "IPv6 literal, without a port, scheme or path"
            )
        hosts.add(host)
    return frozenset(hosts)


def parse_origin(value: str) -> Optional[Origin]:
    """An ``Origin`` header (or an origin setting) split by the comparison rule; ``None`` for a
    missing or ``null`` origin, or one with userinfo, a path, a query or a fragment."""
    match = _ORIGIN.match(value)
    if not match:
        return None
    scheme, host, port = match.group(1).lower(), match.group(2), match.group(3) or ""
    if normalise_host(host) is None:
        return None
    return Origin(scheme, host.lower(), port)


def parse_public_origin(value: str) -> Optional[Origin]:
    """``ui_public_origin``: empty (unset), or an exact ``http(s)://host[:port]``. Anything else,
    an explicit default port included, raises."""
    if value == "":
        return None
    origin = parse_origin(value)
    if origin is None or not value.startswith(f"{origin.scheme}://"):
        raise SettingsError("ui_public_origin must be an exact http(s)://host[:port]")
    if origin.scheme not in _DEFAULT_PORTS:
        raise SettingsError("ui_public_origin must use http or https")
    if origin.port and not 0 < int(origin.port) <= 65535:
        raise SettingsError("ui_public_origin has an invalid port")
    if origin.port == _DEFAULT_PORTS[origin.scheme]:
        raise SettingsError("ui_public_origin must not spell out the default port")
    return origin


class HostHeader(NamedTuple):
    host: str  # canonical (normalise_host): what the allowlist is checked against
    port: str  # as written, empty when absent
    raw_host: str  # as written, lowercased: what the expected origin is built from


def parse_host_header(value: str) -> Optional[HostHeader]:
    """A request's ``Host`` header; ``None`` when it is malformed."""
    match = _HOST_HEADER.match(value)
    if not match:
        return None
    host = normalise_host(match.group(1))
    port = match.group(2) or ""
    if host is None or (port and not 0 < int(port) <= 65535):
        return None
    return HostHeader(host, port, match.group(1).lower())


def expected_origin(public: Optional[Origin], host: HostHeader) -> Origin:
    """``ui_public_origin`` if set, else ``http://`` plus the request's (allowed) ``Host``. Never
    the scheme the server sees, never a forwarded header."""
    return public if public is not None else Origin("http", host.raw_host, host.port)


def origin_matches(header: Optional[str], expected: Origin) -> bool:
    """The one comparison rule (ADR 0003 item 6, shared with ADR 0002)."""
    if header is None:
        return False
    return parse_origin(header) == expected
