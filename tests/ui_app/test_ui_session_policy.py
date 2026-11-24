"""The pure session rules of ADR 0003 items 2, 4 and 6: the owner-token rule, the cookie value
and its MAC, the exact content types and the global rate limit."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from spotdown_ui import session_policy as sp

TOKEN = "a" * 64
NOW = 1_800_000_000.0


def _mac(token: str, expiry: str) -> str:
    key = hmac.new(token.encode(), b"spotdown-session-v1", hashlib.sha256).digest()
    return hmac.new(key, expiry.encode(), hashlib.sha256).hexdigest()


def test_the_cookie_value_is_expiry_and_the_specified_mac() -> None:
    value = sp.make_session(TOKEN, NOW)
    expiry, mac = value.split(".")
    assert expiry == str(int(NOW) + 43200) and mac == _mac(TOKEN, expiry)
    assert sp.session_valid(value, TOKEN, NOW)
    assert sp.session_valid(value, TOKEN, NOW + 43199)


@pytest.mark.parametrize(
    "value",
    [
        f"{int(NOW) - 1}.{_mac(TOKEN, str(int(NOW) - 1))}",  # expired
        f"{int(NOW)}.{_mac(TOKEN, str(int(NOW)))}",  # expires now
        f"{int(NOW) + 43201}.{_mac(TOKEN, str(int(NOW) + 43201))}",  # more than 12 h ahead
        f"1e9.{_mac(TOKEN, '1e9')}",  # non-digit
        f"{'9' * 13}.{_mac(TOKEN, '9' * 13)}",  # over-long expiry
        f"{int(NOW) + 60}.{'0' * 64}",  # tampered MAC
        f"{int(NOW) + 60}.{_mac(TOKEN, str(int(NOW) + 60))[:63]}",  # MAC of the wrong length
        f"{int(NOW) + 60}.{_mac(TOKEN, str(int(NOW) + 60)).upper()}",  # not lowercase hex
        f"{int(NOW) + 60}.{_mac('b' * 64, str(int(NOW) + 60))}",  # made under another token
        f" {int(NOW) + 60}.{_mac(TOKEN, str(int(NOW) + 60))}",
        "",
    ],
)
def test_a_bad_cookie_is_rejected(value: str) -> None:
    assert not sp.session_valid(value, TOKEN, NOW)


def test_the_mac_is_compared_with_compare_digest(monkeypatch) -> None:
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    value = sp.make_session(TOKEN, NOW)
    assert sp.session_valid(value, TOKEN, NOW)
    assert calls == [(value.split(".")[1].encode(), value.split(".")[1].encode())]


def test_the_submitted_token_is_compared_as_utf8_bytes(monkeypatch) -> None:
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert sp.token_matches(TOKEN, TOKEN)
    assert not sp.token_matches("é" * 64, TOKEN)
    assert not sp.token_matches("\ud800" * 64, TOKEN)  # a lone surrogate from JSON: no error
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


def test_a_submitted_token_over_512_characters_never_matches() -> None:
    long = "x" * 513
    assert sp.token_matches("x" * 512, "x" * 512)
    assert not sp.token_matches(long, long)


@pytest.mark.parametrize(
    "raw,rule",
    [(b"", "length"), (b"x" * 31, "length"), (b"x" * 513, "length"), (b"\n\n", "length"),
     (b"x" * 40 + b" y", "character set"), (b"x" * 40 + b'"', "character set"),
     (b"x" * 40 + b"\\", "character set"), (("x" * 40 + "é").encode(), None), (b"\xff" * 40, None),
     (b"x" * 20 + b"\x01" + b"x" * 20, "character set"), (b" " + b"x" * 40, "character set")],
)  # fmt: skip
def test_the_owner_token_rule(raw, rule) -> None:
    token, failed = sp.owner_token_problem(raw)
    assert token is None
    assert failed == (rule or "character set")


def test_owner_tokens_that_satisfy_the_rule() -> None:
    allowed = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in '"\\')
    assert sp.owner_token_problem(allowed.encode())[0] == allowed
    assert sp.owner_token_problem(b"x" * 32 + b"\r\n \t")[0] == "x" * 32  # trailing whitespace
    assert sp.owner_token_problem(b"y" * 512)[0] == "y" * 512


@pytest.mark.parametrize(
    "header,ok",
    [("application/json", True), ("Application/JSON", True),
     ("application/json; charset=utf-8", True),
     ("application/json;charset=UTF-8", True), (None, False), ("text/plain", False),
     ("application/json; charset=latin-1", False), ("application/json; x=1", False),
     ("application/x-www-form-urlencoded", False), ("multipart/form-data; boundary=x", False),
     ("application/jsonx", False), ("application/json, text/plain", False)],
)  # fmt: skip
def test_content_types_are_exact(header, ok) -> None:
    assert sp.content_type_ok(header, "application/json") is ok


def test_cookie_attributes() -> None:
    assert sp.set_cookie_header("v", https=True) == (
        "__Host-spotdown_session=v; Max-Age=43200; Path=/; Secure; HttpOnly; SameSite=Strict"
    )
    assert sp.set_cookie_header("v", https=False) == (
        "spotdown_session=v; Max-Age=43200; Path=/; HttpOnly; SameSite=Strict"
    )


def test_the_rate_limit_closes_after_five_failures_and_reopens() -> None:
    limit = sp.LoginRateLimit()
    for i in range(4):
        limit.record_failure(NOW + i)
        assert limit.retry_after(NOW + i) is None
    limit.record_failure(NOW + 4)
    assert limit.retry_after(NOW + 4) == 60
    assert limit.retry_after(NOW + 63.5) == 1
    assert limit.retry_after(NOW + 64) is None


def test_failures_older_than_the_window_do_not_count() -> None:
    limit = sp.LoginRateLimit()
    for i in range(4):
        limit.record_failure(NOW + i)
    limit.record_failure(NOW + 61)  # the first two are out of the 60 s window
    assert limit.retry_after(NOW + 61) is None


def test_an_attempt_is_checked_compared_and_counted_in_one_step() -> None:
    limit = sp.LoginRateLimit()
    compared = []
    for i in range(5):
        assert limit.attempt(NOW, lambda: compared.append(i) or False) == (None, False)
    assert limit.attempt(NOW, lambda: compared.append("x") or True) == (60, False)
    assert compared == [0, 1, 2, 3, 4]  # closed: the token is not compared
    assert limit.attempt(NOW + 60, lambda: True) == (None, True)
