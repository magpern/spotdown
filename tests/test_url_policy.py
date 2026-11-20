"""The url source's rules: https only, the operator's exact allowlist, no IP literals, no
userinfo, public addresses only, no redirect to another host."""

from __future__ import annotations

import pytest

from app.service import url_policy
from app.service.url_policy import UrlRefused, check_redirect, check_url, is_public_address

ALLOW = frozenset({"media.example.com", "cdn.example.org"})


@pytest.mark.parametrize(
    "url,reason",
    [
        ("http://media.example.com/a.mp3", "https_required"),
        ("ftp://media.example.com/a.mp3", "https_required"),
        ("file:///etc/passwd", "https_required"),
        ("//media.example.com/a.mp3", "https_required"),
        ("https://evil.example.net/a.mp3", "host_not_allowed"),
        ("https://media.example.com.evil.net/a.mp3", "host_not_allowed"),
        ("https://sub.media.example.com/a.mp3", "host_not_allowed"),
        ("https://example.com/a.mp3", "host_not_allowed"),
        ("https://media.example.com./a.mp3", "host_not_allowed"),
        ("https://127.0.0.1/a.mp3", "host_not_allowed"),
        ("https://10.0.0.8/a.mp3", "host_not_allowed"),
        ("https://169.254.169.254/latest/meta-data", "host_not_allowed"),
        ("https://[::1]/a.mp3", "host_not_allowed"),
        ("https://[fe80::1]/a.mp3", "host_not_allowed"),
        ("https://[::ffff:127.0.0.1]/a.mp3", "host_not_allowed"),
        ("https://2130706433/a.mp3", "host_not_allowed"),
        ("https://127.1/a.mp3", "host_not_allowed"),
        ("https://0x7f.0.0.1/a.mp3", "host_not_allowed"),
        ("https://localhost/a.mp3", "host_not_allowed"),
        ("https://%6dedia.example.com/a.mp3", "host_not_allowed"),
        ("https://user:pw@media.example.com/a.mp3", "userinfo_not_allowed"),
        ("https://user@media.example.com/a.mp3", "userinfo_not_allowed"),
        ("https://media.example.com@evil.example.net/a.mp3", "userinfo_not_allowed"),
        ("https://media.example.com\\@evil.example.net/a.mp3", "invalid_url"),
        ("https://media.example.com:8443/a.mp3", "port_not_allowed"),
        ("https://media.example.com:80/a.mp3", "port_not_allowed"),
        ("https://media.example.com:x/a.mp3", "invalid_url"),
        ("https://media.example.com/a b.mp3", "invalid_url"),
        ("https://media.example.com/a\n.mp3", "invalid_url"),
        ("https://médiä.example.com/a.mp3", "invalid_url"),
        ("", "invalid_url"),
        ("https://media.example.com/" + "a" * 2100, "invalid_url"),
    ],
)
def test_refused_urls(url: str, reason: str) -> None:
    with pytest.raises(UrlRefused) as caught:
        check_url(url, ALLOW)
    assert caught.value.reason == reason
    assert url not in str(caught.value) or url == ""


@pytest.mark.parametrize(
    "url,target,ref",
    [
        (
            "https://media.example.com/a/b.mp3?sig=secret#frag",
            "/a/b.mp3?sig=secret",
            "https://media.example.com/a/b.mp3",
        ),
        ("https://MEDIA.example.com:443/x.wav", "/x.wav", "https://media.example.com/x.wav"),
        ("https://cdn.example.org", "/", "https://cdn.example.org"),
        ("https://cdn.example.org/", "/", "https://cdn.example.org"),
    ],
)
def test_accepted_urls_keep_only_host_and_path_in_the_ref(url, target, ref) -> None:
    checked = check_url(url, ALLOW)
    assert checked.target == target
    assert checked.ref == ref
    assert "secret" not in checked.ref


def test_an_empty_allowlist_allows_nothing() -> None:
    with pytest.raises(UrlRefused):
        check_url("https://media.example.com/a.mp3", frozenset())


@pytest.mark.parametrize(
    "location,ok",
    [
        ("/other.mp3", True),
        ("https://media.example.com/other.mp3", True),
        ("other.mp3", True),
        ("https://cdn.example.org/a.mp3", False),  # allowed, but another host
        ("https://evil.example.net/a.mp3", False),
        ("http://media.example.com/a.mp3", False),
        ("https://127.0.0.1/a.mp3", False),
        ("//evil.example.net/a.mp3", False),
        ("https://user@media.example.com/a.mp3", False),
        ("", False),
    ],
)
def test_redirects_stay_on_the_same_host(location: str, ok: bool) -> None:
    current = "https://media.example.com/dir/a.mp3"
    if ok:
        assert check_redirect(current, location, ALLOW).startswith("https://media.example.com/")
    else:
        with pytest.raises(UrlRefused) as caught:
            check_redirect(current, location, ALLOW)
        assert caught.value.reason == "redirect_refused"


@pytest.mark.parametrize(
    "address,public",
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("127.8.9.10", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("255.255.255.255", False),
        ("224.0.0.1", False),
        ("192.0.2.1", False),
        ("198.18.0.1", False),
        ("::1", False),
        ("::", False),
        ("fe80::1", False),
        ("fe80::1%eth0", False),
        ("fc00::1", False),
        ("fd12:3456::1", False),
        ("ff02::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:93.184.216.34", True),
        ("2002:7f00:1::1", False),  # 6to4 wrapping 127.0.0.1
        ("2001::1", False),  # Teredo
        ("64:ff9b::7f00:1", False),
        ("not-an-ip", False),
    ],
)
def test_public_addresses(address: str, public: bool) -> None:
    assert is_public_address(address) is public


def test_all_public_needs_every_address_public() -> None:
    assert url_policy.all_public(["93.184.216.34"])
    assert not url_policy.all_public([])
    assert not url_policy.all_public(["93.184.216.34", "10.0.0.1"])


def test_allowlist_parsing() -> None:
    assert url_policy.parse_allowlist(" Media.Example.com, ,cdn.example.org ") == ALLOW
    assert url_policy.parse_allowlist("") == frozenset()
    for bad in ("127.0.0.1", "*.example.com", "localhost", "a_b.example.com", "[::1]"):
        with pytest.raises(ValueError):
            url_policy.parse_allowlist(bad)
