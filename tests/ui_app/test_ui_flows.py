"""The UI's flows end to end against a faked backend (spotdown#78 acceptance 1, 2 and 4): link to
tracks to match to analysis to mp3 download, and local file to analysis. Every outbound request
is recorded: it goes to ``SPOTDOWN_BACKEND_URL``'s host only, on a fixed route, with the key in
its ``Authorization`` header and nowhere else; no response, header, cookie or log line carries
the key."""

from __future__ import annotations

import logging
from typing import List

import httpx
import pytest

from .helpers import AUDIO_BYTES, KEY, SPOTIFY_ID, VIDEO, Ui

ROUTES = {
    ("POST", "/v2/spotify/resolve"),
    ("POST", "/v2/match"),
    ("PUT", "/v2/uploads"),
    ("POST", "/v2/analyses"),
    ("GET", "/v2/jobs/job_1"),
    ("GET", "/v2/jobs/job_a"),
    ("GET", "/v2/analyses/an_1"),
    ("POST", "/v2/audio"),
    ("GET", "/v2/audio/au_1"),
}


@pytest.fixture
def ui(tmp_path) -> Ui:
    return Ui(tmp_path)


def _link_flow(client) -> List[httpx.Response]:
    out = []
    res = client.post(
        "/api/spotify/resolve", json={"url": f"https://open.spotify.com/track/{SPOTIFY_ID}"}
    )
    out.append(res)
    assert res.status_code == 200, res.text
    track = res.json()["tracks"][0]
    assert track["spotify_id"] == SPOTIFY_ID and res.json()["truncated"] is False
    assert res.json()["listed"] == 2 and res.json()["tracks"][1]["duration_ms"] is None
    res = client.post("/api/match", json={"tracks": [{"title": track["title"], "artists":
                      track["artists"], "duration_s": track["duration_ms"] / 1000,
                      "isrc": track["isrc"]}]})  # fmt: skip
    out.append(res)
    assert res.status_code == 200, res.text
    best = res.json()["results"][0]["best"]
    assert best["source"] == f"youtube:{VIDEO}"
    body = {
        "source_ref": best["source"],
        "reference": {"kind": "spotify", "id": SPOTIFY_ID, "duration_s": 213.0},
    }
    res = client.post("/api/analyses", json=body)
    out.append(res)
    assert res.status_code == 202 and res.json()["job_id"] == "job_1"
    res = client.get("/api/jobs/job_1")
    out.append(res)
    assert res.json()["status"] == "done" and res.json()["analysis_id"] == "an_1"
    res = client.get("/api/analyses/an_1")
    out.append(res)
    assert res.status_code == 200 and res.json()["schema_version"] == "analysis-v1"
    res = client.post("/api/audio", json={"source_ref": best["source"], "bitrate": 192,
                      "title": track["title"], "artists": track["artists"]})  # fmt: skip
    out.append(res)
    assert res.status_code == 202 and res.json() == {"job_id": "job_a", "status": "queued"}
    res = client.get("/api/audio-jobs/job_a")
    out.append(res)
    assert res.json()["audio_id"] == "au_1"
    res = client.get("/api/audio/au_1")
    out.append(res)
    assert res.status_code == 200 and res.content == AUDIO_BYTES
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.headers["content-disposition"] == 'attachment; filename="spotdown-au_1.mp3"'
    return out


def _file_flow(client) -> List[httpx.Response]:
    out = []
    res = client.put("/api/uploads", content=b"RIFF....WAVEfmt ", headers={"Content-Type":
                     "application/octet-stream"})  # fmt: skip
    out.append(res)
    assert res.status_code == 201, res.text
    upload_id = res.json()["upload_id"]
    res = client.post("/api/analyses", json={"upload_id": upload_id})
    out.append(res)
    assert res.status_code == 202
    out += [client.get("/api/jobs/job_1"), client.get("/api/analyses/an_1")]
    assert out[-1].json()["schema_version"] == "analysis-v1"
    return out


def test_link_to_tracks_to_match_to_analysis_to_mp3(ui) -> None:
    with ui.signed() as client:
        _link_flow(client)
    seen = ui.backend.seen
    assert [(s.method, s.path) for s in seen] == [
        ("POST", "/v2/spotify/resolve"), ("POST", "/v2/match"), ("POST", "/v2/analyses"),
        ("GET", "/v2/jobs/job_1"), ("GET", "/v2/analyses/an_1"), ("POST", "/v2/audio"),
        ("GET", "/v2/jobs/job_a"), ("GET", "/v2/audio/au_1"),
    ]  # fmt: skip
    import json

    match = json.loads(seen[1].body)
    assert match["cache_scope"] == "spotdown_ui" and match["tracks"][0]["client_ref"] == "0"
    analysis = json.loads(seen[2].body)
    assert analysis == {
        "cache_scope": "spotdown_ui",
        "source_ref": f"youtube:{VIDEO}",
        "reference": {"kind": "spotify", "id": SPOTIFY_ID, "duration_s": 213.0},
    }
    assert json.loads(seen[5].body) == {"source_ref": f"youtube:{VIDEO}", "bitrate": 192,
                                        "title": "Song <i>", "artists": ["Artist"]}  # fmt: skip
    assert seen[3].query == seen[4].query == "cache_scope=spotdown_ui"
    assert seen[6].query == ""  # an mp3 job has no scope (docs/contract-v2.md, POST /v2/audio)


def test_each_job_is_polled_in_its_own_scope(ui) -> None:
    """The backend shows a job only in the scope that made it: an analysis job in spotdown_ui, an
    mp3 job in none. Polling either at the other's route is a 404, never another scope's job."""
    with ui.signed() as client:
        assert client.get("/api/jobs/job_1").status_code == 200
        assert client.get("/api/audio-jobs/job_a").status_code == 200
        assert client.get("/api/jobs/job_a").status_code == 404
        assert client.get("/api/audio-jobs/job_1").status_code == 404
    assert [s.query for s in ui.backend.seen] == [
        "cache_scope=spotdown_ui",
        "",
        "cache_scope=spotdown_ui",
        "",
    ]


def test_a_bare_track_is_matched_without_invented_values(ui) -> None:
    import json

    with ui.signed() as client:
        bare = client.post("/api/spotify/resolve", json={"url": "x"}).json()["tracks"][1]
        assert bare["artists"] == [] and bare["duration_ms"] is None and bare["isrc"] is None
        res = client.post("/api/match", json={"tracks": [{"title": bare["title"], "artists": []}]})
        assert res.status_code == 200
    sent = json.loads(ui.backend.seen[-1].body)["tracks"][0]
    assert sent == {"client_ref": "0", "title": "Bare", "artists": []}


def test_local_file_to_analysis(ui) -> None:
    with ui.signed() as client:
        _file_flow(client)
    upload, analyse = ui.backend.seen[0], ui.backend.seen[1]
    assert (upload.method, upload.path, upload.query) == (
        "PUT",
        "/v2/uploads",
        "cache_scope=spotdown_ui",
    )
    assert (
        upload.body == b"RIFF....WAVEfmt "
        and upload.headers["content-type"] == "application/octet-stream"
    )
    import json

    assert json.loads(analyse.body) == {"cache_scope": "spotdown_ui", "upload_id": "up_" + "a" * 32}


def test_each_create_request_gets_a_fresh_idempotency_key(ui) -> None:
    with ui.signed() as client:
        _file_flow(client)
        _file_flow(client)
        _link_flow(client)
    keys = [s.headers.get("idempotency-key") for s in ui.backend.seen
            if (s.method, s.path) in {("PUT", "/v2/uploads"), ("POST", "/v2/analyses"),
                                      ("POST", "/v2/audio")}]  # fmt: skip
    assert len(keys) == 6 and all(keys) and len(set(keys)) == 6
    for k in keys:
        assert k is not None and k.startswith("ui-") and len(k) == 35


def test_every_outbound_request_goes_to_the_backend_host_only(ui) -> None:
    with ui.signed() as client:
        _link_flow(client)
        _file_flow(client)
        client.get("/healthz")
        client.get("/")
    assert ui.backend.seen
    for s in ui.backend.seen:
        assert (s.scheme, s.host, s.port) == ("http", "spotdown-api", 8000), s
        assert (s.method, s.path) in ROUTES, s


def test_healthz_is_open_and_never_calls_the_backend(ui) -> None:
    res = ui.anon().get("/healthz")
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert ui.backend.seen == []


def test_the_key_goes_only_into_the_authorization_header(ui) -> None:
    with ui.signed() as client:
        _link_flow(client)
        _file_flow(client)
    for s in ui.backend.seen:
        assert s.headers["authorization"] == "Bearer " + KEY
        rest = {k: v for k, v in s.headers.items() if k != "authorization"}
        assert KEY not in repr(rest) and KEY.encode() not in s.body
        assert KEY not in s.path + s.query


def _no_key(res: httpx.Response) -> None:
    encoded = [KEY.encode(), KEY.encode("unicode_escape")]
    for needle in encoded:
        assert needle not in res.content, res.request.url
    for name, value in res.headers.multi_items():
        assert KEY not in name and KEY not in value, (res.request.url, name)


def test_the_key_is_in_no_response_header_cookie_or_log(ui, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    responses: List[httpx.Response] = []
    with ui.anon() as anon:
        responses += [anon.get("/"), anon.get("/login"), anon.get("/healthz"), anon.get("/ui.css"),
                      anon.post("/api/match", json={})]  # fmt: skip
    with ui.signed() as client:
        responses += _link_flow(client) + _file_flow(client)
        for path in ("/", "/ui_api.js", "/ui_link.js", "/ui_upload.js", "/nope", "/api/jobs/x.y",
                     "/api/audio/missing", "/api/analyses/an_2"):  # fmt: skip
            responses.append(client.get(path))
        responses.append(client.post("/api/analyses", json={"upload_id": "../x"}))
        ui.backend.overrides[("POST", "/v2/match")] = httpx.Response(
            401, json={"error": {"code": "unauthorized", "message": KEY}}
        )
        responses.append(
            client.post("/api/match", json={"tracks": [{"title": "t", "duration_s": 1}]})
        )
        responses.append(client.post("/logout", json={}))
    assert (
        responses[-2].status_code == 502 and responses[-2].json()["error"]["code"] == "backend_auth"
    )
    for res in responses:
        _no_key(res)
        assert (
            "x-backend-internal" not in res.headers
            and res.headers.get("set-cookie", "").find("backend=") < 0
        )
    out = capsys.readouterr()
    assert KEY not in caplog.text and KEY not in out.out and KEY not in out.err
    assert KEY not in repr(ui.app.state.backend)


def test_backend_errors_are_shown_as_their_code_only(ui) -> None:
    ui.backend.overrides[("POST", "/v2/spotify/resolve")] = httpx.Response(
        422,
        json={"error": {"code": "spotify_disabled", "message": "secret detail <x>"}},
        headers={"retry-after": "5"},
    )
    ui.backend.overrides[("POST", "/v2/match")] = httpx.Response(500, text="Traceback: boom")
    with ui.signed() as client:
        res = client.post("/api/spotify/resolve", json={"url": "x"})
        assert res.status_code == 422
        assert res.json() == {"error": {"code": "spotify_disabled",
                                        "message": "the backend refused the request"}}  # fmt: skip
        assert "retry-after" not in res.headers
        res = client.post("/api/match", json={"tracks": [{"title": "t", "duration_s": 1}]})
        assert res.status_code == 502 and res.json()["error"]["code"] == "backend_error"
        assert "boom" not in res.text


def test_an_unreachable_backend_is_a_fixed_502(tmp_path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to http://spotdown-api:8000/v2/match " + KEY)

    from spotdown_ui.app import create_app

    from .helpers import ui_env

    env = ui_env(tmp_path)
    app = create_app(env, httpx.MockTransport(down))
    ui = Ui.__new__(Ui)
    ui.env, ui.app = env, app
    with ui.signed() as client:
        res = client.post("/api/match", json={"tracks": [{"title": "t", "duration_s": 1}]})
    assert res.status_code == 502 and res.json()["error"]["code"] == "backend_unreachable"
    assert KEY not in res.text and "spotdown-api" not in res.text


def test_a_non_mp3_answer_is_not_relayed_as_audio(ui) -> None:
    ui.backend.overrides[("GET", "/v2/audio/au_1")] = httpx.Response(
        200, content=b"<html>", headers={"content-type": "text/html"}
    )
    with ui.signed() as client:
        res = client.get("/api/audio/au_1")
    assert res.status_code == 502 and res.headers["content-type"] == "application/json"
