"""Pure rules for the UI's exposure checks (ADR 0003 items 5 and 6, carried over by ADR 0005).

Parses and validates the ``UI_ALLOWED_HOSTS`` and ``UI_PUBLIC_ORIGIN`` settings (one origin or a
list, spotdown#105), a request's ``Host`` header and its ``Origin`` header, picks the expected
origin by ``Host``, and compares origins by the one rule ADR 0002 shares.
No I/O.
"""

from __future__ import annotations

import ipaddress
import re
from typing import FrozenSet, NamedTuple, Optional, Tuple

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
    """A UI setting is invalid (raised at construction)."""


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
    """``UI_ALLOWED_HOSTS``: comma-separated DNS names (RFC 1123 labels), IPv4 literals or
    bracketed IPv6 literals. An empty entry, ``*``, a port, a scheme, a path or whitespace
    raises."""
    hosts = set()
    for entry in value.split(","):
        if not entry or entry != entry.strip() or re.search(r"\s", entry):
            raise SettingsError("UI_ALLOWED_HOSTS: an entry is empty or has whitespace")
        host = normalise_host(entry)
        if host is None:
            raise SettingsError(
                "UI_ALLOWED_HOSTS: each entry must be a DNS name, an IPv4 literal or a bracketed "
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
    """``UI_PUBLIC_ORIGIN``: empty (unset), or an exact ``http(s)://host[:port]``. Anything else,
    an explicit default port included, raises."""
    if value == "":
        return None
    origin = parse_origin(value)
    if origin is None or not value.startswith(f"{origin.scheme}://"):
        raise SettingsError("UI_PUBLIC_ORIGIN must be an exact http(s)://host[:port]")
    if origin.scheme not in _DEFAULT_PORTS:
        raise SettingsError("UI_PUBLIC_ORIGIN must use http or https")
    if origin.port and not 0 < int(origin.port) <= 65535:
        raise SettingsError("UI_PUBLIC_ORIGIN has an invalid port")
    if origin.port == _DEFAULT_PORTS[origin.scheme]:
        raise SettingsError("UI_PUBLIC_ORIGIN must not spell out the default port")
    return origin


def parse_public_origins(value: str, allowed_hosts: FrozenSet[str]) -> Tuple[Origin, ...]:
    """``UI_PUBLIC_ORIGIN``: empty (unset, ``()``), one origin (exactly as before), or a
    comma-separated list of origins (spotdown#105). Each entry is checked by
    ``parse_public_origin``. Whitespace is rejected, never trimmed, as in ``UI_ALLOWED_HOSTS``.

    A list of two or more must also have no duplicate host and port, one scheme for every entry
    (so the cookie choice stays fixed), and every host in ``UI_ALLOWED_HOSTS``. A single value is
    not checked against the allowlist: it is the expected origin for every allowed ``Host``, as
    before, so a proxy that rewrites ``Host`` keeps working."""
    if value == "":
        return ()
    origins = []
    for entry in value.split(","):
        if not entry or re.search(r"\s", entry):
            raise SettingsError("UI_PUBLIC_ORIGIN: an entry is empty or has whitespace")
        origin = parse_public_origin(entry)
        if origin is None:  # unreachable: only "" parses to None
            raise SettingsError("UI_PUBLIC_ORIGIN: an entry is empty")
        origins.append(origin)
    if len(origins) == 1:
        return (origins[0],)
    keys = {(normalise_host(o.host), o.port) for o in origins}
    if len(keys) != len(origins):
        raise SettingsError("UI_PUBLIC_ORIGIN: two entries have the same host and port")
    if len({o.scheme for o in origins}) != 1:
        raise SettingsError("UI_PUBLIC_ORIGIN: every entry must use the same scheme")
    if any(normalise_host(o.host) not in allowed_hosts for o in origins):
        raise SettingsError("UI_PUBLIC_ORIGIN: every entry's host must be in UI_ALLOWED_HOSTS")
    return tuple(origins)


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


def expected_origin(public: Tuple[Origin, ...], host: HostHeader) -> Optional[Origin]:
    """The origin an unsafe request from the (allowed) ``Host`` must carry. No public origin:
    ``http://`` plus that ``Host``. One: that origin, for every allowed ``Host``. A list: the entry
    whose host and port equal the ``Host``'s, or ``None`` (nothing matches) when no entry does.
    Never the scheme the server sees, never a forwarded header."""
    if not public:
        return Origin("http", host.raw_host, host.port)
    if len(public) == 1:
        return public[0]
    for origin in public:
        if normalise_host(origin.host) == host.host and origin.port == host.port:
            return origin
    return None


def origin_matches(header: Optional[str], expected: Optional[Origin]) -> bool:
    """The one comparison rule (ADR 0003 item 6, shared with ADR 0002). No expected origin
    matches nothing."""
    if header is None or expected is None:
        return False
    return parse_origin(header) == expected
