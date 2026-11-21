"""The ``Host`` allowlist and the one ``Origin`` comparison rule of the standalone app.

ADR 0003 item 6 (shared with ADR 0002 Decision 2): both sides are split into scheme, host and
port; the scheme and host are lowercased and the three parts must be equal as strings. No default
port is added or removed, and a missing ``Origin``, ``Origin: null`` or one with userinfo, a path,
a query or a fragment never matches. The expected origin is ``ui_public_origin`` if set, else
``http://`` plus the request's ``Host``; never the scheme the server sees or a forwarded header.

Pure: no I/O.
"""

from __future__ import annotations

import ipaddress
import re
from typing import FrozenSet, Optional, Tuple

Origin = Tuple[str, str, str]  # (scheme, host, port), the port "" when absent

DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,[::1]"

_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_PORT = re.compile(r"^[0-9]{1,5}$")
_ORIGIN = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#@]+)$")


def _host_name(text: str) -> Optional[str]:
    """A DNS name, an IPv4 literal or a bracketed IPv6 literal, lowercased; IPv6 unbracketed."""
    if text.startswith("[") and text.endswith("]"):
        try:
            return str(ipaddress.IPv6Address(text[1:-1]))
        except ValueError:
            return None
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        pass
    if len(text) > 253 or not all(_LABEL.match(label) for label in text.split(".")):
        return None
    return text.lower()


def split_host(value: str) -> Optional[Tuple[str, str]]:
    """``host[:port]`` as (host, port), or None if it is not exactly that."""
    if not value or value != value.strip():
        return None
    if value.startswith("["):
        end = value.find("]")
        name, rest = value[: end + 1], value[end + 1 :]
        if end < 0 or (rest and not rest.startswith(":")):
            return None
        port = rest[1:] if rest else ""
    elif ":" in value:
        name, port = value.split(":", 1)
    else:
        name, port = value, ""
    if (port or value.endswith(":")) and not _PORT.match(port):
        return None
    host = _host_name(name)
    return (host, port) if host else None


def parse_allowed_hosts(value: str) -> FrozenSet[str]:
    """The allowlist from a comma-separated setting. A bad entry (empty, ``*``, a port, a scheme,
    a path, whitespace) raises ValueError, so a misconfiguration fails at construction."""
    hosts = set()
    for entry in value.split(","):
        parsed = split_host(entry)
        if parsed is None or parsed[1]:
            raise ValueError("ui_allowed_hosts: each entry is a host name or an IP literal")
        hosts.add(parsed[0])
    return frozenset(hosts)


def host_allowed(host_header: Optional[str], allowed: FrozenSet[str]) -> bool:
    parsed = split_host(host_header or "")
    return parsed is not None and parsed[0] in allowed


def parse_origin(value: Optional[str]) -> Optional[Origin]:
    """``scheme://host[:port]`` with nothing else, or None (``null`` and absent included)."""
    match = _ORIGIN.match(value or "")
    if not match:
        return None
    parsed = split_host(match.group(2))
    if parsed is None:
        return None
    return (match.group(1).lower(), parsed[0], parsed[1])


def expected_origin(public_origin: str, host_header: Optional[str]) -> Optional[Origin]:
    if public_origin:
        return parse_origin(public_origin)
    return parse_origin("http://" + (host_header or ""))


def origin_matches(origin: Optional[str], public_origin: str, host_header: Optional[str]) -> bool:
    got = parse_origin(origin)
    want = expected_origin(public_origin, host_header)
    return got is not None and want is not None and got == want
