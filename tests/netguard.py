"""Block every non-loopback network access during tests.

Tests never use the network or real accounts (AGENTS.md, safety envelope). ``install`` replaces
the socket entry points that open a connection or resolve a name, so a test that tries to reach
a non-loopback address fails before anything is sent. Loopback addresses and Unix sockets stay
allowed, because local fakes (a loopback HTTP server, a local Redis fake) need them.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})


class NetworkBlockedError(RuntimeError):
    """A test tried to reach a non-loopback host."""


def _is_loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower().rstrip(".") in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _check_address(address: object) -> None:
    # AF_UNIX addresses are str or bytes paths; AF_INET/INET6 addresses are tuples.
    if isinstance(address, tuple):
        if address and _is_loopback(address[0]):
            return
        raise NetworkBlockedError(f"network access blocked in tests: {address[:2]!r}")
    # A path (Unix socket) is local by definition.


@contextmanager
def install() -> Iterator[None]:
    """Patch ``socket`` for the duration of the block, then restore it."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname

    def connect(self: socket.socket, address: Any) -> None:
        _check_address(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check_address(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _is_loopback(host):
            raise NetworkBlockedError(f"name lookup blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host: str) -> str:
        if not _is_loopback(host):
            raise NetworkBlockedError(f"name lookup blocked in tests: {host!r}")
        return real_gethostbyname(host)

    patches: list[tuple[object, str, Callable[..., Any]]] = [
        (socket.socket, "connect", connect),
        (socket.socket, "connect_ex", connect_ex),
        (socket, "getaddrinfo", getaddrinfo),
        (socket, "gethostbyname", gethostbyname),
    ]
    originals = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    for obj, name, fn in patches:
        setattr(obj, name, fn)
    try:
        yield
    finally:
        for obj, name, fn in originals:
            setattr(obj, name, fn)
