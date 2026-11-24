"""The settings and header rules of ADR 0003 items 5 and 6 in the UI image (pure; spotdown#52
"Settings", "Origin shape" and "Host"; moved from the retired standalone app by spotdown#79)."""

from __future__ import annotations

import pytest

from spotdown_ui.app import create_app
from spotdown_ui.exposure_policy import (
    Origin,
    SettingsError,
    origin_matches,
    parse_allowed_hosts,
    parse_host_header,
    parse_public_origin,
)

from .helpers import ui_env


def test_the_default_allowed_hosts() -> None:
    assert parse_allowed_hosts("localhost,127.0.0.1,[::1]") == {"localhost", "127.0.0.1", "[::1]"}
    assert parse_allowed_hosts("Spot.Example,[0:0::1]") == {"spot.example", "[::1]"}


@pytest.mark.parametrize(
    "value",
    ["", "localhost,", ",localhost", "*", "host:8000", "http://host", "host/path", " localhost",
     "local host", "localhost\t", "[::1]:8000", "::1", "256.1.1.1", "a..b", "-a.example", "[x]"],
)  # fmt: skip
def test_a_bad_allowed_hosts_entry_raises_at_construction(tmp_path, value) -> None:
    with pytest.raises(SettingsError):
        parse_allowed_hosts(value)
    with pytest.raises(SettingsError):
        create_app(ui_env(tmp_path, UI_ALLOWED_HOSTS=value))
    assert not (tmp_path / "data" / "ui_token").exists()  # no app object, no token


@pytest.mark.parametrize(
    "value",
    ["https://h/", "https://u@h", "https://h?q", "https://h#f", "http://h:80", "https://h:443",
     "ftp://h", "HTTPS://h", "https://", "https://h:0", "https://h:99999", "h", "https://h/x",
     " https://h", "https://h:8443/"],
)  # fmt: skip
def test_a_public_origin_that_is_not_exact_raises_at_construction(tmp_path, value) -> None:
    with pytest.raises(SettingsError):
        parse_public_origin(value)
    with pytest.raises(SettingsError):
        create_app(ui_env(tmp_path, UI_PUBLIC_ORIGIN=value))


def test_exact_public_origins() -> None:
    assert parse_public_origin("") is None
    assert parse_public_origin("https://spot.example") == Origin("https", "spot.example", "")
    assert parse_public_origin("http://h:8080") == Origin("http", "h", "8080")
    assert parse_public_origin("https://[::1]:8443") == Origin("https", "[::1]", "8443")


@pytest.mark.parametrize(
    "value,host,port",
    [("localhost:8000", "localhost", "8000"), ("127.0.0.1:8000", "127.0.0.1", "8000"),
     ("[::1]:8000", "[::1]", "8000"), ("[::1]", "[::1]", ""), ("LocalHost", "localhost", "")],
)  # fmt: skip
def test_host_headers_are_parsed(value, host, port) -> None:
    parsed = parse_host_header(value)
    assert parsed is not None and (parsed.host, parsed.port) == (host, port)


@pytest.mark.parametrize(
    "value", ["", "::1", "[::1", "a b", "localhost:", "localhost:x", "localhost:99999", "[::1]x",
              "u@localhost", "localhost/x", "[zz]:80"],
)  # fmt: skip
def test_a_malformed_host_header_is_none(value) -> None:
    assert parse_host_header(value) is None


EXPECTED = Origin("http", "localhost", "8000")


@pytest.mark.parametrize(
    "header",
    [None, "null", "", "http://localhost:8000/", "http://u@localhost:8000",
     "http://localhost:8000?x", "http://localhost:8000#f", "https://localhost:8000",
     "http://localhost", "http://localhost:80", "http://localhost:9999", "http://evil.example"],
)  # fmt: skip
def test_only_the_exact_origin_matches(header) -> None:
    assert not origin_matches(header, EXPECTED)


def test_the_comparison_lowercases_scheme_and_host() -> None:
    assert origin_matches("http://localhost:8000", EXPECTED)
    assert origin_matches("HTTP://LOCALHOST:8000", EXPECTED)
    assert not origin_matches("http://localhost", Origin("http", "localhost", "80"))


def test_the_comparison_does_not_normalise_beyond_lowercase() -> None:
    expected = Origin("http", "[::1]", "8000")
    assert origin_matches("http://[::1]:8000", expected)
    assert not origin_matches("http://[0:0::1]:8000", expected)  # equal as strings, nothing more
    host = parse_host_header("[0:0::1]:8000")
    assert host is not None and host.host == "[::1]" and host.raw_host == "[0:0::1]"
