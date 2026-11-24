"""The UI's pure rules (``spotdown_ui.api_policy``, ``headers``, ``healthcheck.probe_host``): what
the browser may send, what the backend gets, and what of the backend's answer reaches the
browser."""

from __future__ import annotations

import json

import pytest

from spotdown_ui import api_policy as policy
from spotdown_ui.headers import CSP, with_headers
from spotdown_ui.healthcheck import probe_host


@pytest.mark.parametrize("ident", ["job_1", "an_" + "f" * 32, "a", "A-b_9", "x" * 64])
def test_good_ids(ident) -> None:
    assert policy.checked_id(ident) == ident


@pytest.mark.parametrize("ident", ["", "..", "a/b", "a.b", "x" * 65, "a%2f", "é", "a\n", " a"])
def test_bad_ids(ident) -> None:
    with pytest.raises(policy.Refused) as err:
        policy.checked_id(ident)
    assert err.value.status == 404


def test_relay_passes_a_success_object_and_its_status() -> None:
    raw = json.dumps({"job_id": "j"}).encode()
    assert policy.relay(202, "application/json", raw) == (202, {"job_id": "j"})


@pytest.mark.parametrize("status", [401, 403])
def test_relay_turns_a_refused_key_into_502(status) -> None:
    assert policy.relay(status, "application/json", b"{}") == policy.BACKEND_KEY


@pytest.mark.parametrize(
    "status,ctype,raw",
    [(200, "text/html", b"{}"), (200, "application/json", b"[1]"), (200, None, b"{}"),
     (200, "application/json", b"not json"), (500, "application/json", b"{}"),
     (500, "application/json", b'{"error": {"code": "x"}}'), (302, "application/json", b"{}"),
     (502, "application/json", b"{}"), (503, "application/json", b'{"error": {"code": "<x>"}}')],
)  # fmt: skip
def test_relay_refuses_what_it_cannot_vouch_for(status, ctype, raw) -> None:
    assert policy.relay(status, ctype, raw) == policy.BACKEND_ERROR


@pytest.mark.parametrize(
    "code,shown",
    [("spotify_disabled", "spotify_disabled"), ("Bad Code", "backend_error"), (5, "backend_error"),
     ("<script>", "backend_error"), ("x" * 65, "backend_error")],
)  # fmt: skip
def test_relay_shows_only_a_well_formed_code(code, shown) -> None:
    raw = json.dumps({"error": {"code": code, "message": "secret <b>"}, "extra": 1}).encode()
    status, body = policy.relay(422, "application/json; charset=utf-8", raw)
    assert status == 422
    assert body == {"error": {"code": shown, "message": "the backend refused the request"}}


@pytest.mark.parametrize(
    "status,code", [(502, "provider_error"), (503, "queue_unavailable")]
)  # fmt: skip
def test_relay_shows_the_contracts_own_5xx_codes(status, code) -> None:
    raw = json.dumps({"error": {"code": code, "message": "the Spotify request failed"}}).encode()
    shown = {"error": {"code": code, "message": "the backend refused the request"}}
    assert policy.relay(status, "application/json", raw) == (status, shown)


def test_the_match_body_is_rebuilt_from_known_fields() -> None:
    body = policy.match_body({"tracks": [{"title": "t", "artists": ["a"], "duration_s": 2,
                                          "isrc": "X", "explicit": True, "z": 1}]})  # fmt: skip
    assert body == {
        "cache_scope": "spotdown_ui",
        "tracks": [
            {
                "client_ref": "0",
                "title": "t",
                "artists": ["a"],
                "duration_s": 2.0,
                "isrc": "X",
                "explicit": True,
            }
        ],
    }


@pytest.mark.parametrize("artists", [None, []])
def test_a_track_without_duration_or_artists_is_passed_on_without_them(artists) -> None:
    body = policy.match_body({"tracks": [{"title": "t", "artists": artists, "duration_s": None}]})
    assert body["tracks"] == [{"client_ref": "0", "title": "t"}]
    assert policy.match_body({"tracks": [{"title": "t"}]})["tracks"][0] == {
        "client_ref": "0", "title": "t"}  # fmt: skip


def test_the_match_track_follows_the_backend_limits() -> None:
    ok = policy.match_body({"tracks": [{"title": "t" * 300, "artists": ["a" * 200] * 10}]})
    assert len(ok["tracks"][0]["artists"]) == 10
    for bad in ({"title": "t" * 301}, {"title": "t", "artists": ["a"] * 11},
                {"title": "t", "artists": ["a" * 201]}):  # fmt: skip
        with pytest.raises(policy.Refused):
            policy.match_body({"tracks": [bad]})


@pytest.mark.parametrize(
    "tracks",
    [[], [{}] * 51, "x", [{"title": "", "duration_s": 1}], [{"title": "t", "duration_s": 0}],
     [{"title": "t", "duration_s": float("inf")}], [{"title": "t", "duration_s": True}],
     [{"title": "t", "duration_s": 1, "artists": [1]}], [{"title": "t" * 513, "duration_s": 1}]],
)  # fmt: skip
def test_bad_match_bodies(tracks) -> None:
    with pytest.raises(policy.Refused):
        policy.match_body({"tracks": tracks})


def test_the_audio_tags_follow_the_contract_limits() -> None:
    body = policy.audio_body({"source_ref": "youtube:dQw4w9WgXcQ", "title": "t" * 1000,
                              "artists": ["a" * 1000] * 50})  # fmt: skip
    assert len(body["title"]) == 1000 and len(body["artists"]) == 50
    for bad in ({"title": "t" * 1001}, {"artists": ["a"] * 51}, {"artists": ["a" * 1001]}):
        with pytest.raises(policy.Refused):
            policy.audio_body({"source_ref": "youtube:dQw4w9WgXcQ", **bad})


def test_the_audio_body_defaults_to_320() -> None:
    assert policy.audio_body({"source_ref": "youtube:dQw4w9WgXcQ"}) == {
        "source_ref": "youtube:dQw4w9WgXcQ", "bitrate": 320}  # fmt: skip


@pytest.mark.parametrize("raw", [b"[]", b"1", b"nope", b'"x"', b"[" * 100000])
def test_a_body_must_be_a_json_object(raw) -> None:
    with pytest.raises(policy.Refused):
        policy.parse_json(raw)


def test_headers_keep_a_page_csp_and_override_framing() -> None:
    own = [(b"content-security-policy", b"default-src 'none'"), (b"x-frame-options", b"ALLOW")]
    out = dict(with_headers(own))
    assert out[b"content-security-policy"] == b"default-src 'none'"
    assert out[b"x-frame-options"] == b"DENY" and out[b"cache-control"] == b"no-store"
    assert dict(with_headers([]))[b"content-security-policy"] == CSP.encode()


@pytest.mark.parametrize(
    "hosts,host",
    [(None, "localhost:8000"), ("ui.example,localhost", "ui.example:8000"),
     ("[0::1]", "[::1]:8000"), ("bad host", "127.0.0.1:8000"), ("", "127.0.0.1:8000")],
)  # fmt: skip
def test_the_healthcheck_sends_an_allowed_host(hosts, host) -> None:
    env = {} if hosts is None else {"UI_ALLOWED_HOSTS": hosts}
    assert probe_host(env) == host
