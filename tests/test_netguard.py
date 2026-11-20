from __future__ import annotations

import socket

import pytest

from app.schemas import TrackMeta

from .netguard import NetworkBlockedError


def test_track_meta_builds() -> None:
    meta = TrackMeta(id="t1", title="Song")
    assert meta.id == "t1"
    assert meta.title == "Song"


def test_non_loopback_connect_is_blocked() -> None:
    with socket.socket() as s, pytest.raises(NetworkBlockedError):
        s.connect(("203.0.113.1", 80))


def test_name_lookup_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_loopback_connect_is_allowed() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())
