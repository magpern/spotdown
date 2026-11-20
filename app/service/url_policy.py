"""Rules for the ``url`` source (pure: no DNS, no network).

https only, port 443, no userinfo, a DNS name (never an IP literal) on the operator's exact-match
allowlist, and every address it resolves to must be public. Redirects may only stay on the same
host. The provenance ref keeps host and path only (no userinfo, query or fragment).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import FrozenSet, Iterable
from urllib.parse import urljoin, urlsplit

MAX_URL_LENGTH = 2048
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


class UrlRefused(ValueError):
    """The URL breaks a rule. ``reason`` is a stable code; it never contains the URL."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CheckedUrl:
    host: str  # lower case DNS name, no trailing dot
    target: str  # path plus query, what is requested ("/" when empty)
    ref: str  # provenance: https://host/path, no userinfo, query or fragment


def is_dns_name(host: str) -> bool:
    """A multi-label lower-case ASCII DNS name that is not an IP literal."""
    if not host or len(host) > 253 or host.endswith(".") or _is_ip_literal(host):
        return False
    labels = host.split(".")
    return len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        # Short or numeric IPv4 forms ("127.1", "2130706433") that resolvers may still accept.
        return bool(re.fullmatch(r"[0-9.]+|0x[0-9a-f.x]+", host))
    return True


def parse_allowlist(raw: str) -> FrozenSet[str]:
    """``URL_ALLOWED_HOSTS`` as a set of exact DNS names; anything else is an operator error."""
    hosts = set()
    for item in raw.split(","):
        host = item.strip().lower()
        if not host:
            continue
        if not is_dns_name(host):
            raise ValueError("URL_ALLOWED_HOSTS holds an entry that is not a DNS name")
        hosts.add(host)
    return frozenset(hosts)


def check_url(url: str, allowed_hosts: FrozenSet[str]) -> CheckedUrl:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise UrlRefused("invalid_url")
    if _FORBIDDEN_CHARS.search(url) or not url.isascii():
        raise UrlRefused("invalid_url")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UrlRefused("https_required")
    if "@" in parts.netloc:
        raise UrlRefused("userinfo_not_allowed")
    try:
        port = parts.port
    except ValueError:
        raise UrlRefused("invalid_url") from None
    if port not in (None, 443):
        raise UrlRefused("port_not_allowed")
    host = (parts.hostname or "").lower()
    if not host or _is_ip_literal(host):
        raise UrlRefused("host_not_allowed")
    if not is_dns_name(host) or host not in allowed_hosts:
        raise UrlRefused("host_not_allowed")
    path = parts.path or "/"
    if not path.startswith("/"):
        raise UrlRefused("invalid_url")
    target = path + ("?" + parts.query if parts.query else "")
    ref = "https://" + host + (parts.path if parts.path not in ("", "/") else "")
    return CheckedUrl(host=host, target=target, ref=ref)


def check_redirect(current_url: str, location: str, allowed_hosts: FrozenSet[str]) -> str:
    """The absolute URL of a redirect, if it stays on the same host; refused otherwise."""
    if not location:
        raise UrlRefused("redirect_refused")
    nxt = urljoin(current_url, location)
    try:
        checked = check_url(nxt, allowed_hosts)
    except UrlRefused:
        raise UrlRefused("redirect_refused") from None
    if checked.host != check_url(current_url, allowed_hosts).host:
        raise UrlRefused("redirect_refused")
    return nxt


def is_public_address(address: str) -> bool:
    """True only for a globally routable unicast address (IPv4-mapped IPv6 is unwrapped)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None or ip.teredo is not None:
            return False
    return bool(ip.is_global) and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def all_public(addresses: Iterable[str]) -> bool:
    found = list(addresses)
    return bool(found) and all(is_public_address(a) for a in found)
