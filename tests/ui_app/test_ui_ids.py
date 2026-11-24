"""Hostile ids and bodies never reach a backend path (spotdown#78 acceptance 4), and the backend
client cannot be steered elsewhere (no environment proxy, no redirect)."""

from __future__ import annotations

import json

import httpx
import pytest

from .helpers import VIDEO, Ui

HOSTILE = ["..", ".", "../x", "..%2fx", "%2e%2e", "a.b", "x" * 65, "a%2fb", "a b", "a%00b",
           "%252e%252e", "job_1;x", "job_1%3Fcache_scope=other", "%3F", "é"]  # fmt: skip
READS = ["/api/jobs/{}", "/api/audio-jobs/{}", "/api/analyses/{}", "/api/audio/{}"]


@pytest.fixture
def ui(tmp_path) -> Ui:
    return Ui(tmp_path)


@pytest.mark.parametrize("template", READS)
def test_hostile_ids_never_reach_a_backend_path(ui, template) -> None:
    with ui.signed() as client:
        for ident in HOSTILE:
            res = client.get(template.format(ident))
            assert res.status_code in (404, 405), (ident, res.status_code)
    assert ui.backend.seen == [], ui.backend.paths()


def test_a_browser_query_string_is_not_passed_on(ui) -> None:
    with ui.signed() as client:
        assert client.get("/api/jobs/job_1?cache_scope=other&x=1").status_code == 200
    assert [(s.path, s.query) for s in ui.backend.seen] == [
        ("/v2/jobs/job_1", "cache_scope=spotdown_ui")
    ]


def test_a_good_id_is_passed_on_verbatim(ui) -> None:
    with ui.signed() as client:
        client.get("/api/jobs/job_Ab-9")
        client.get("/api/analyses/" + "a" * 64)
    assert ui.backend.paths() == [("GET", "/v2/jobs/job_Ab-9"), ("GET", "/v2/analyses/" + "a" * 64)]


@pytest.mark.parametrize(
    "body",
    [{"upload_id": "../../etc"}, {"upload_id": "x" * 65}, {"upload_id": 5},
     {"source_ref": "url:https://evil.example/a.mp3"}, {"source_ref": "youtube:../../../../x"},
     {"source_ref": f"youtube:{VIDEO}x"}, {"upload_id": "up_1", "source_ref": f"youtube:{VIDEO}"},
     {}, {"source_ref": f"youtube:{VIDEO}", "reference": {"kind": "spotify", "id": "../x",
                                                          "duration_s": 1}},
     {"source_ref": f"youtube:{VIDEO}", "reference": {"kind": "other", "id": "x", "duration_s": 1}},
     {"upload_id": "up_1", "explicit": "yes"}],
)  # fmt: skip
def test_a_hostile_analysis_body_is_refused_before_the_backend(ui, body) -> None:
    with ui.signed() as client:
        res = client.post("/api/analyses", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"
    assert ui.backend.seen == []


@pytest.mark.parametrize(
    "body",
    [{"source_ref": "url:https://evil.example"}, {"source_ref": f"youtube:{VIDEO}", "bitrate": 64},
     {"source_ref": f"youtube:{VIDEO}", "bitrate": True}, {"source_ref": f"youtube:{VIDEO}",
                                                         "artists": "x"}],
)  # fmt: skip
def test_a_hostile_audio_body_is_refused_before_the_backend(ui, body) -> None:
    with ui.signed() as client:
        assert client.post("/api/audio", json=body).status_code == 422
    assert ui.backend.seen == []


def test_only_known_fields_reach_the_backend(ui) -> None:
    with ui.signed() as client:
        body = {"upload_id": "up_1", "cache_scope": "someone_else", "url": "https://evil.example",
                "host": "evil.example", "headers": {"Authorization": "x"}}  # fmt: skip
        assert client.post("/api/analyses", json=body).status_code == 202
        track = {"title": "t", "duration_s": 1, "client_ref": "../x", "extra": 1}
        client.post("/api/match", json={"tracks": [track], "cache_scope": "other"})
        client.post("/api/spotify/resolve", json={"url": "https://open.spotify.com/x", "host": "e"})
    sent = [json.loads(s.body) for s in ui.backend.seen]
    assert sent[0] == {"cache_scope": "spotdown_ui", "upload_id": "up_1"}
    assert sent[1] == {"cache_scope": "spotdown_ui", "tracks": [{"client_ref": "0", "title": "t",
                       "duration_s": 1.0}]}  # fmt: skip
    assert sent[2] == {"url": "https://open.spotify.com/x"}
    for s in ui.backend.seen:
        assert s.host == "spotdown-api" and "evil" not in s.path + s.query


def test_browser_headers_are_not_passed_on(ui) -> None:
    with ui.signed() as client:
        headers = {
            "Authorization": "Bearer browser",
            "Idempotency-Key": "chosen",
            "X-Forwarded-Host": "evil.example",
            "Host": "localhost:8000",
        }
        client.post("/api/analyses", json={"upload_id": "up_1"}, headers=headers)
    sent = ui.backend.seen[0].headers
    assert sent["authorization"] != "Bearer browser" and sent["idempotency-key"] != "chosen"
    assert "x-forwarded-host" not in sent and "cookie" not in sent and "origin" not in sent
    assert sent["host"] == "spotdown-api:8000"


def test_a_json_body_over_the_cap_is_413_and_not_sent(ui) -> None:
    with ui.signed() as client:
        res = client.post("/api/spotify/resolve", content=b'{"url": "' + b"x" * 70000 + b'"}',
                          headers={"Content-Type": "application/json"})  # fmt: skip
    assert res.status_code == 413 and ui.backend.seen == []


def test_the_client_ignores_the_environment_and_redirects(ui, monkeypatch) -> None:
    client = ui.app.state.backend._client
    assert client.trust_env is False and client.follow_redirects is False
    ui.backend.overrides[("GET", "/v2/jobs/job_1")] = httpx.Response(
        302, headers={"location": "http://evil.example/v2/jobs/job_1"}
    )
    with ui.signed() as signed:
        res = signed.get("/api/jobs/job_1")
    assert res.status_code == 502
    assert [s.host for s in ui.backend.seen] == ["spotdown-api"]
