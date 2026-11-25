"""``UI_PUBLIC_ORIGIN`` as a list of origins matched by the request's ``Host`` (spotdown#105): one
value behaves as before, each listed origin is accepted only from its own ``Host``, a bad list
stops the app at start-up, and an allowed ``Host`` with no listed origin is refused on unsafe
requests."""

from __future__ import annotations

import ipaddress

import pytest

from spotdown_ui.app import create_app
from spotdown_ui.exposure_policy import (
    Origin,
    SettingsError,
    browser_ipv6,
    expected_origin,
    origin_matches,
    parse_allowed_hosts,
    parse_host_header,
    parse_public_origins,
)
from spotdown_ui.settings import parse_settings

from .helpers import Ui, ui_env

A, B = "https://a.example.org", "https://b.example.net"
HOSTS = "a.example.org,b.example.net,c.example.com"  # c is allowed but has no listed origin
JSON = {"Content-Type": "application/json"}


def _login(ui: Ui, host: str, origin: str):
    client = ui.anon(base_url=f"http://{host}")
    headers = {"X-Spotdown-UI": "1", "Origin": origin, **JSON}
    return client.post("/login", json={"token": ui.token}, headers=headers)


def _host(value: str):
    parsed = parse_host_header(value)
    assert parsed is not None
    return parsed


@pytest.fixture
def two(tmp_path) -> Ui:
    return Ui(tmp_path, UI_ALLOWED_HOSTS=HOSTS, UI_PUBLIC_ORIGIN=f"{A},{B}")


# --- the pure rule --------------------------------------------------------------------------------


def test_the_expected_origin_is_picked_by_host() -> None:
    allowed = parse_allowed_hosts(HOSTS)
    public = parse_public_origins(f"{A},{B}", allowed)
    assert public == (Origin("https", "a.example.org", ""), Origin("https", "b.example.net", ""))
    assert expected_origin(public, _host("a.example.org")) == public[0]
    assert expected_origin(public, _host("B.Example.Net")) == public[1]
    assert expected_origin(public, _host("c.example.com")) is None
    assert expected_origin(public, _host("a.example.org:8443")) is None  # the port must equal


def test_one_value_and_unset_are_unchanged() -> None:
    allowed = parse_allowed_hosts("localhost")
    assert parse_public_origins("", allowed) == ()
    assert expected_origin((), _host("localhost:8000")) == Origin("http", "localhost", "8000")
    one = parse_public_origins(A, allowed)  # not in the allowlist: a single value is not checked
    assert one == (Origin("https", "a.example.org", ""),)
    assert expected_origin(one, _host("localhost")) == one[0]
    assert expected_origin(one, _host("other.example")) == one[0]


def test_listed_ports_match_the_host_port() -> None:
    allowed = parse_allowed_hosts("a.example.org,[::1]")
    public = parse_public_origins("http://a.example.org:8080,http://[::1]:9000", allowed)
    assert expected_origin(public, _host("a.example.org:8080")) == public[0]
    assert expected_origin(public, _host("a.example.org")) is None
    assert expected_origin(public, _host("[::1]:9000")) == public[1]
    assert origin_matches("http://[::1]:9000", expected_origin(public, _host("[::1]:9000")))


# --- spotdown#107: an IPv6 origin must be written as a browser writes it --------------------------


@pytest.mark.parametrize(
    "text,browser",
    [("::1", "::1"), ("0:0::1", "::1"), ("::", "::"), ("::0001", "::1"),
     ("2001:DB8:0:1:1:1:1:1", "2001:db8:0:1:1:1:1:1"),  # one zero group is not compressed
     ("2001:db8::1:1:1:1:1", "2001:db8:0:1:1:1:1:1"),
     ("1:0:0:2:0:0:0:3", "1:0:0:2::3"),  # the longest run
     ("1:0:0:2:0:0:3:4", "1::2:0:0:3:4"),  # the first of two equal runs
     ("::ffff:127.0.0.1", "::ffff:7f00:1"),  # never a dotted IPv4 tail
     ("::127.0.0.1", "::7f00:1"), ("fe80::", "fe80::"), ("1:2:3:4:5:6:7:8", "1:2:3:4:5:6:7:8")],
)  # fmt: skip
def test_browser_ipv6_is_the_whatwg_serialisation(text, browser) -> None:
    assert browser_ipv6(ipaddress.IPv6Address(text)) == browser


NON_CANONICAL_IPV6 = ["http://[0:0::1]:9000", "http://[::0001]", "http://[::ffff:127.0.0.1]",
                      "http://[2001:db8::1:1:1:1:1]", "http://[1::2:0:0:0:3]"]  # fmt: skip


@pytest.mark.parametrize("value", NON_CANONICAL_IPV6)
@pytest.mark.parametrize("shape", ["{v}", "http://a.example.org,{v}", "{v},http://a.example.org"])
def test_a_non_canonical_ipv6_origin_stops_the_app_at_start_up(tmp_path, value, shape) -> None:
    value = shape.format(v=value)  # one value (stated in the PR: it never matched) and in a list
    hosts = "a.example.org,[::1],[::ffff:7f00:1],[2001:db8:0:1:1:1:1:1],[1:0:0:2::3]"
    with pytest.raises(SettingsError, match="write the IPv6 host as"):
        parse_public_origins(value, parse_allowed_hosts(hosts))
    with pytest.raises(SettingsError):
        parse_settings(ui_env(tmp_path, UI_ALLOWED_HOSTS=hosts, UI_PUBLIC_ORIGIN=value))
    with pytest.raises(SettingsError):
        create_app(ui_env(tmp_path, UI_ALLOWED_HOSTS=hosts, UI_PUBLIC_ORIGIN=value))
    assert not (tmp_path / "data" / "ui_token").exists()


@pytest.mark.parametrize(
    "value",
    [
        "http://[::1]:9000",
        "http://[::ffff:7f00:1]",
        "http://[2001:db8:0:1:1:1:1:1]",
        "http://[1:0:0:2::3]",
        "http://[::A]",
    ],  # fmt: skip
)
def test_a_canonical_ipv6_origin_is_accepted(value) -> None:
    assert len(parse_public_origins(value, frozenset())) == 1


def test_a_browser_signs_in_on_a_listed_ipv6_origin(tmp_path) -> None:
    ui = Ui(tmp_path, UI_ALLOWED_HOSTS="a.example.org,[::1]",
            UI_PUBLIC_ORIGIN="http://a.example.org:8080,http://[::1]:9000")  # fmt: skip
    client = ui.anon()  # the test client cannot take an IPv6 base URL: send the Host header
    for origin, status in (("http://[::1]:9000", 204), ("http://[0:0::1]:9000", 403)):
        headers = {"Host": "[::1]:9000", "X-Spotdown-UI": "1", "Origin": origin, **JSON}
        res = client.post("/login", json={"token": ui.token}, headers=headers)
        assert res.status_code == status


# --- AC 1: one value ------------------------------------------------------------------------------


def test_one_value_is_expected_from_every_allowed_host(tmp_path) -> None:
    ui = Ui(tmp_path, UI_ALLOWED_HOSTS="ui.example,other.example", UI_PUBLIC_ORIGIN=A)
    for host in ("ui.example", "other.example"):
        res = _login(ui, host, A)
        assert res.status_code == 204
        assert res.headers["set-cookie"].startswith("__Host-spotdown_session=")
        assert _login(ui, host, f"https://{host}").status_code == 403


# --- AC 2: two origins ----------------------------------------------------------------------------


@pytest.mark.parametrize("host,origin", [("a.example.org", A), ("b.example.net", B)])
def test_each_listed_origin_signs_in_from_its_own_host(two, host, origin) -> None:
    res = _login(two, host, origin)
    assert res.status_code == 204
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("__Host-spotdown_session=") and "; Secure" in cookie
    assert "domain" not in cookie.lower()  # per host: a login on one name is not one on the other


@pytest.mark.parametrize("host,origin", [("a.example.org", B), ("b.example.net", A)])
def test_the_other_listed_origin_is_403(two, host, origin) -> None:
    res = _login(two, host, origin)
    assert res.status_code == 403
    assert "set-cookie" not in res.headers


@pytest.mark.parametrize(
    "host,origin,status",
    [("a.example.org", A, 204), ("a.example.org", B, 403), ("b.example.net", B, 204),
     ("b.example.net", A, 403)],
)  # fmt: skip
def test_unsafe_requests_need_their_own_hosts_origin(two, host, origin, status) -> None:
    own = A if host == "a.example.org" else B
    cookie = _login(two, host, own).headers["set-cookie"].split(";", 1)[0]
    client = two.anon(base_url=f"http://{host}")
    headers = {"X-Spotdown-UI": "1", "Origin": origin, "Cookie": cookie, **JSON}
    assert client.post("/logout", content=b"{}", headers=headers).status_code == status


def test_forwarded_headers_do_not_pick_the_origin(two) -> None:
    client = two.anon(base_url="http://c.example.com")
    headers = {"X-Spotdown-UI": "1", "Origin": A, "X-Forwarded-Host": "a.example.org",
               "Forwarded": "host=a.example.org;proto=https", **JSON}  # fmt: skip
    assert client.post("/login", json={"token": two.token}, headers=headers).status_code == 403


@pytest.mark.parametrize(
    "forwarded",
    [{"X-Forwarded-Host": "b.example.net"}, {"Forwarded": "host=b.example.net"},
     {"X-Forwarded-Host": "b.example.net", "Forwarded": "host=b.example.net;proto=https"}],
)  # fmt: skip
def test_forwarded_headers_do_not_pick_another_listed_origin(two, forwarded) -> None:
    """spotdown#107: ``Host: a`` with a forwarded ``b`` and ``b``'s origin is 403; the same
    request with ``a``'s origin signs in, so only the selection refuses it."""
    client = two.anon(base_url="http://a.example.org")
    for origin, status in ((B, 403), (A, 204)):
        headers = {"X-Spotdown-UI": "1", "Origin": origin, **forwarded, **JSON}
        res = client.post("/login", json={"token": two.token}, headers=headers)
        assert res.status_code == status


# --- AC 4: an allowed Host with no listed origin --------------------------------------------------


def test_an_allowed_host_without_an_origin_is_403_on_unsafe_requests(two) -> None:
    for origin in (A, B, "https://c.example.com", "http://c.example.com", "null", "c.example.com"):
        assert _login(two, "c.example.com", origin).status_code == 403
    cookie = _login(two, "a.example.org", A).headers["set-cookie"].split(";", 1)[0]
    client = two.anon(base_url="http://c.example.com")
    for origin in (A, "https://c.example.com", "null"):
        headers = {"X-Spotdown-UI": "1", "Origin": origin, "Cookie": cookie, **JSON}
        assert client.post("/api/match", json={}, headers=headers).status_code == 403
        assert client.post("/logout", content=b"{}", headers=headers).status_code == 403
    assert two.backend.seen == []


def test_an_allowed_host_without_an_origin_serves_get_pages_as_today(two) -> None:
    client = two.anon(base_url="http://c.example.com")
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    res = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert (res.status_code, res.headers["location"]) == (303, "/login")
    assert client.get("/ui.css").status_code == 401
    cookie = _login(two, "a.example.org", A).headers["set-cookie"].split(";", 1)[0]
    assert client.get("/ui.css", headers={"Cookie": cookie}).status_code == 200


# --- AC 3: start-up fails closed ------------------------------------------------------------------


BAD_LISTS = [
    f"{A},https://d.example.org",  # a host not in UI_ALLOWED_HOSTS
    f"{A},http://b.example.net",  # mixed schemes
    f"http://a.example.org,{B}",
    f"{A},{A}",  # duplicates
    f"{A},https://A.Example.Org",
    f"{A},{B},{A}",
    f"{A},",  # empty entries
    f",{A}",
    f"{A},,{B}",
    ",",
    f"{A}, {B}",  # whitespace is rejected, not trimmed
    f"{A} ,{B}",
    f"{A},{B}\t",
]
# Today's invalid single-origin forms (test_ui_exposure_policy.py), each inside a list.
BAD_SINGLE = ["https://h/", "https://u@h", "https://h?q", "https://h#f", "http://h:80",
              "https://h:443", "ftp://h", "HTTPS://h", "https://", "https://h:0",
              "https://h:99999", "h", "https://h/x", " https://h", "https://h:8443/"]  # fmt: skip


@pytest.mark.parametrize(
    "value", BAD_LISTS + [f"{A},{bad}" for bad in BAD_SINGLE] + [f"{bad},{B}" for bad in BAD_SINGLE]
)
def test_a_bad_list_stops_the_app_at_start_up(tmp_path, value) -> None:
    hosts = HOSTS + ",h"
    with pytest.raises(SettingsError):
        parse_public_origins(value, parse_allowed_hosts(hosts))
    with pytest.raises(SettingsError):
        parse_settings(ui_env(tmp_path, UI_ALLOWED_HOSTS=hosts, UI_PUBLIC_ORIGIN=value))
    with pytest.raises(SettingsError):
        create_app(ui_env(tmp_path, UI_ALLOWED_HOSTS=hosts, UI_PUBLIC_ORIGIN=value))
    assert not (tmp_path / "data" / "ui_token").exists()  # no app object, no token


def test_a_good_list_reaches_the_settings(tmp_path) -> None:
    settings = parse_settings(ui_env(tmp_path, UI_ALLOWED_HOSTS=HOSTS, UI_PUBLIC_ORIGIN=f"{A},{B}"))
    assert [o.host for o in settings.public_origins] == ["a.example.org", "b.example.net"]
    assert parse_settings(ui_env(tmp_path)).public_origins == ()
