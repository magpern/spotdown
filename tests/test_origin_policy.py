"""The Host allowlist and the one Origin comparison rule (ADR 0003 item 6, ADR 0002 Decision 2)."""

from __future__ import annotations

import pytest

from app import origin_policy as op

DEFAULT = op.parse_allowed_hosts(op.DEFAULT_ALLOWED_HOSTS)


def test_the_default_allowlist_is_the_loopback_names() -> None:
    assert DEFAULT == {"localhost", "127.0.0.1", "::1"}


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "LOCALHOST:1", "[::1]:8000", "[::1]", "127.0.0.1:80"]
)
def test_allowed_hosts_any_port(host: str) -> None:
    assert op.host_allowed(host, DEFAULT)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example",
        "localhost.evil.example",
        "::1",
        "localhost:",
        "localhost:x",
        "user@localhost",
        " localhost",
        "[::1",
        "[::1]x",
        "127.0.0.1:8000:1",
        "localhost/x",
    ],
)
def test_other_or_unparsable_hosts_are_refused(host) -> None:
    assert not op.host_allowed(host, DEFAULT)


@pytest.mark.parametrize(
    "entry", ["", "*", "localhost:8000", "http://localhost", "a/b", "a b", "localhost,"]
)
def test_a_bad_allowlist_entry_raises(entry: str) -> None:
    with pytest.raises(ValueError):
        op.parse_allowed_hosts(entry)


@pytest.mark.parametrize(
    "origin,public,host,ok",
    [
        ("http://localhost:8000", "", "localhost:8000", True),
        ("HTTP://LocalHost:8000", "", "localhost:8000", True),
        ("http://localhost:9999", "", "localhost:8000", False),
        ("http://localhost", "", "localhost:80", False),  # no default port added or removed
        ("https://localhost:8000", "", "localhost:8000", False),  # never the seen scheme
        (None, "", "localhost:8000", False),
        ("null", "", "localhost:8000", False),
        ("http://localhost:8000/", "", "localhost:8000", False),
        ("http://u@localhost:8000", "", "localhost:8000", False),
        ("http://localhost:8000?x", "", "localhost:8000", False),
        ("https://spotdown.example", "https://spotdown.example", "localhost:8000", True),
        ("http://spotdown.example", "https://spotdown.example", "spotdown.example", False),
        ("http://localhost:8000", "https://spotdown.example", "localhost:8000", False),
        ("http://[::1]:8000", "", "[::1]:8000", True),
    ],
)
def test_the_origin_rule(origin, public: str, host: str, ok: bool) -> None:
    assert op.origin_matches(origin, public, host) is ok
