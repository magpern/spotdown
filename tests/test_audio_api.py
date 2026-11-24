"""``POST /v2/audio`` and ``GET /v2/audio/{id}``: the gate, the request checks, the key,
idempotency and the download headers. yt-dlp and the transcode are faked; no network."""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.service import audio_policy

from .audio_helpers import FAKE_MP3, REF, VIDEO, AudioStack
from .service_helpers import AUTH, assert_valid


@pytest.fixture()
def stack(tmp_path) -> AudioStack:
    return AudioStack(tmp_path)


def _queued(stack: AudioStack) -> int:
    with stack.store._conn() as conn:
        jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        requests = conn.execute("SELECT COUNT(*) FROM audio_requests").fetchone()[0]
    return jobs + requests + len(stack.dispatch.job_ids)


def test_a_job_ends_with_an_audio_id_and_the_mp3_downloads(stack: AudioStack) -> None:
    res = stack.request({"source_ref": REF, "bitrate": 192, "title": "Song", "artists": ["A"]})
    assert res.status_code == 202 and set(res.json()) == {"job_id", "status"}
    job = stack.poll(res)
    assert_valid("job-v1", job)
    assert job["status"] == "done" and "analysis_id" not in job
    assert audio_policy.is_audio_id(job["audio_id"])
    assert stack.transcodes[0]["bitrate"] == 192 and stack.world.downloads == [VIDEO]

    got = stack.fetch(job["audio_id"])
    assert got.status_code == 200
    assert got.headers["content-type"] == "audio/mpeg"
    assert got.headers["content-disposition"] == 'attachment; filename="A - Song.mp3"'
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.content.endswith(FAKE_MP3)  # the ID3 tag comes first


def test_the_bitrate_defaults_to_320(stack: AudioStack) -> None:
    stack.audio_id(stack.request())
    assert stack.transcodes[0]["bitrate"] == 320


def test_source_disabled_when_ytdlp_sourcing_is_off(tmp_path) -> None:
    stack = AudioStack(tmp_path, allow_ytdlp_sourcing=False)
    res = stack.request()
    assert res.status_code == 422 and res.json()["error"]["code"] == "source_disabled"
    assert _queued(stack) == 0 and stack.world.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"source_ref": "url:https://media.example.com/a.mp3"},
        {"source_ref": "https://www.youtube.com/watch?v=" + VIDEO},
        {"source_ref": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"},
        {"source_ref": "upload:up_1"},
        {"source_ref": "youtube:short"},
        {"source_ref": "youtube:" + VIDEO + "x"},
        {"source_ref": "youtube:../../etc/pa"},
        {"source_ref": REF, "bitrate": 160},
        {"source_ref": REF, "bitrate": "320"},
        {"source_ref": REF, "bitrate": 0},
        {"source_ref": REF, "url": "https://media.example.com/a.mp3"},
        {"bitrate": 320},
    ],
)
def test_invalid_requests_queue_nothing(stack: AudioStack, body: Dict[str, Any]) -> None:
    res = stack.request(body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request", res.json()
    assert _queued(stack) == 0 and stack.world.calls == []


def test_an_idempotency_key_is_required(stack: AudioStack) -> None:
    res = stack.client.post("/v2/audio", headers=AUTH, json={"source_ref": REF})
    assert res.status_code == 422 and _queued(stack) == 0


def test_the_same_key_and_body_replay_the_same_job(stack: AudioStack) -> None:
    first = stack.request({"source_ref": REF, "bitrate": 256}, key="same")
    again = stack.request({"source_ref": REF, "bitrate": 256}, key="same")
    assert again.status_code == 202 and again.json() == first.json()
    assert len(stack.dispatch.job_ids) == 1


def test_the_same_key_with_another_body_is_refused(stack: AudioStack) -> None:
    stack.request({"source_ref": REF, "bitrate": 256}, key="same")
    other = stack.request({"source_ref": REF, "bitrate": 128}, key="same")
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    # A key used for an analysis is refused for audio too (the route is part of the hash).
    stack.post({"source_ref": REF}, key="shared")
    assert stack.request(key="shared").status_code == 409
    assert len(stack.dispatch.job_ids) == 2


def test_a_queue_failure_leaves_nothing(tmp_path) -> None:
    stack = AudioStack(tmp_path)

    def down(job_id: str) -> None:
        raise ConnectionError("redis is down")

    stack.client.app.state.dispatch = down  # type: ignore[attr-defined]
    res = stack.request()
    assert res.status_code == 503
    assert _queued(stack) == 0 and stack.store.idem_get("", "k-1") is None


@pytest.mark.parametrize(
    "method,path", [("post", "/v2/audio"), ("get", "/v2/audio/au_" + "0" * 32)]
)
def test_without_the_key_both_routes_are_401(stack: AudioStack, method: str, path: str) -> None:
    for headers in ({}, {"Authorization": "Bearer wrong-key-wrong-key-wrong-key-wrong"}):
        res = getattr(stack.client, method)(path, headers=headers)
        assert res.status_code == 401 and res.headers["www-authenticate"] == "Bearer"
    assert _queued(stack) == 0


@pytest.mark.parametrize(
    "audio_id",
    [
        "au_" + "0" * 32,  # well formed, unknown
        "au_" + "0" * 31,
        "AU_" + "0" * 32,
        "..",
        "%2e%2e",
        "..%2f..%2fservice.db",
        "%2fetc%2fpasswd",
        "au_" + "0" * 32 + ".mp3",
        "service.db",
    ],
)
def test_unknown_malformed_and_path_like_ids_are_404(stack: AudioStack, audio_id: str) -> None:
    stack.settings.audio_dir.mkdir(parents=True, exist_ok=True)
    (stack.settings.audio_dir / ("au_" + "0" * 32 + ".mp3")).write_bytes(b"orphan, no row")
    res = stack.client.get("/v2/audio/" + audio_id, headers=AUTH)
    assert res.status_code == 404 and res.json()["error"]["code"] in ("not_found",)
    assert "audio" not in res.headers["content-type"]


def test_a_path_with_a_slash_never_reaches_the_route(stack: AudioStack) -> None:
    raw = TestClient(stack.client.app)
    for path in ("/v2/audio/a/b", "/v2/audio/../jobs/x", "/v2/audio//etc/passwd"):
        res = raw.get(path, headers=AUTH)
        assert res.status_code in (404, 405) and "audio" not in res.headers["content-type"]


def test_the_file_name_is_safe_ascii(stack: AudioStack) -> None:
    body = {
        "source_ref": REF,
        "title": 'Sång "x"\r\nX-Evil: 1; filename=../../a\x00',
        "artists": ["Björk/Ö", "‮evil"],
    }
    got = stack.fetch(stack.audio_id(stack.request(body)))
    disposition = got.headers["content-disposition"]
    name = disposition.removeprefix('attachment; filename="').removesuffix('"')
    assert disposition.isascii() and "\r" not in disposition and "\n" not in disposition
    assert name.endswith(".mp3") and "/" not in name and '"' not in name and ".." not in name
    assert "Bjork" in name and "Sang" in name


def test_other_routes_still_never_return_audio(stack: AudioStack) -> None:
    res = stack.request()
    audio_id = stack.audio_id(res)
    paths = [
        f"/v2/jobs/{res.json()['job_id']}",
        f"/v2/analyses/{audio_id}",
        f"/v2/analyses/lookup?source_ref={REF}",
    ]
    for path in paths:
        got = stack.client.get(path, headers=AUTH)
        assert "audio" not in got.headers.get("content-type", ""), path
        assert FAKE_MP3 not in got.content, path
