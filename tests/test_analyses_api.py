"""POST /v2/analyses and GET /v2/jobs/{id} for the upload and url sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service.store import Store
from app.service.worker import execute_job

from .service_helpers import (
    AUTH,
    SCOPE,
    RecordingDispatch,
    assert_valid,
    audio_files,
    client_for,
    service_settings,
    wav_bytes,
)

OCTET = {**AUTH, "Content-Type": "application/octet-stream"}


@pytest.fixture()
def settings(tmp_path: Path):
    return service_settings(tmp_path)


def _upload(client, tmp_path: Path, scope: str = SCOPE, seconds: float = 2.0) -> str:
    res = client.put(
        f"/v2/uploads?cache_scope={scope}", headers=OCTET, content=wav_bytes(tmp_path, seconds)
    )
    assert res.status_code == 201
    return res.json()["upload_id"]


def test_an_upload_analysis_is_queued_and_polled(settings, tmp_path: Path) -> None:
    dispatch = RecordingDispatch()
    client = client_for(settings, dispatch)
    upload_id = _upload(client, tmp_path)
    res = client.post(
        "/v2/analyses",
        headers=AUTH,
        json={"cache_scope": SCOPE, "client_ref": "track-7", "upload_id": upload_id},
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    assert res.json() == {"job_id": job_id, "status": "queued"}
    assert dispatch.job_ids == [job_id]
    job = client.get(f"/v2/jobs/{job_id}?cache_scope={SCOPE}", headers=AUTH).json()
    assert job == {
        "schema_version": "job-v1",
        "job_id": job_id,
        "status": "queued",
        "outcome": None,
        "client_ref": "track-7",
    }
    assert_valid("job-v1", job)


def test_end_to_end_upload_to_a_valid_analysis(settings, tmp_path: Path) -> None:
    """The real sandbox and core analyser on fixture audio; the result is schema-valid."""
    store = Store(settings.service_db_path)
    dispatch = RecordingDispatch(then=lambda job_id: execute_job(job_id, settings, store))
    client = client_for(settings, dispatch)
    upload_id = _upload(client, tmp_path, seconds=12.0)
    job_id = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
    ).json()["job_id"]
    job = client.get(f"/v2/jobs/{job_id}?cache_scope={SCOPE}", headers=AUTH).json()
    assert job["status"] == "done", job
    assert_valid("job-v1", job)
    doc = store.get_analysis(job["analysis_id"])
    assert_valid("analysis-v1", doc)
    assert doc is not None
    assert doc["provenance"]["source"] == {"kind": "upload"}
    assert doc["provenance"]["run"]["job_id"] == job_id
    assert 100 < doc["tempo"]["bpm"] < 140
    assert doc["provenance"]["audio"]["sha256"] == store_sha(tmp_path)
    assert "meter" not in doc["grid"] and "alignment" not in doc
    assert audio_files(settings) == []
    assert store.get_upload(upload_id) is None


def store_sha(tmp_path: Path) -> str:
    import hashlib

    return hashlib.sha256((tmp_path / "fixture.wav").read_bytes()).hexdigest()


def test_a_url_analysis_is_queued_without_keeping_the_query(settings) -> None:
    client = client_for(settings)
    url = "https://media.example.com/a/b.mp3?X-Signature=secret"
    res = client.post("/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "url": url})
    assert res.status_code == 202
    job = Store(settings.service_db_path).get_job(res.json()["job_id"])
    assert job is not None
    assert job["source_kind"] == "url" and job["source_ref"] == "https://media.example.com/a/b.mp3"
    view = client.get(f"/v2/jobs/{job['job_id']}?cache_scope={SCOPE}", headers=AUTH)
    assert "secret" not in view.text


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"cache_scope": SCOPE}, 422, "invalid_request"),
        (
            {"cache_scope": SCOPE, "upload_id": "up_1", "url": "https://media.example.com/a"},
            422,
            "invalid_request",
        ),
        ({"cache_scope": SCOPE, "source_ref": "youtube:dQw4w9WgXcQ"}, 422, "source_disabled"),
        ({"source_ref": "youtube:dQw4w9WgXcQ"}, 422, "source_disabled"),
        ({"upload_id": "up_1"}, 422, "scope_required"),
        ({"url": "https://media.example.com/a.mp3"}, 422, "scope_required"),
        ({"cache_scope": "", "upload_id": "up_1"}, 422, "scope_required"),
        ({"cache_scope": "a b", "upload_id": "up_1"}, 422, "invalid_request"),
        ({"cache_scope": SCOPE, "url": "http://media.example.com/a.mp3"}, 422, "url_not_allowed"),
        ({"cache_scope": SCOPE, "url": "https://evil.example.net/a.mp3"}, 422, "url_not_allowed"),
        ({"cache_scope": SCOPE, "url": "https://10.0.0.1/a.mp3"}, 422, "url_not_allowed"),
        ({"cache_scope": SCOPE, "url": "https://[::1]/a.mp3"}, 422, "url_not_allowed"),
        ({"cache_scope": SCOPE, "url": "https://u:p@media.example.com/a"}, 422, "url_not_allowed"),
        ({"cache_scope": SCOPE, "upload_id": "up_missing"}, 404, "upload_not_found"),
        ({"cache_scope": SCOPE, "upload_id": "../../etc/passwd"}, 422, "invalid_request"),
        (
            {
                "cache_scope": SCOPE,
                "upload_id": "up_1",
                "reference": {"kind": "k", "id": "i", "duration_s": 1},
            },
            422,
            "alignment_unavailable",
        ),
        (
            {"cache_scope": SCOPE, "upload_id": "up_1", "extra": "https://x/?sig=leak"},
            422,
            "invalid_request",
        ),
    ],
)
def test_refused_requests(settings, body, status, code) -> None:
    dispatch = RecordingDispatch()
    res = client_for(settings, dispatch).post("/v2/analyses", headers=AUTH, json=body)
    assert res.status_code == status, res.text
    assert res.json()["error"]["code"] == code
    assert set(res.json()) == {"error"}
    assert "leak" not in res.text and "sig=" not in res.text
    assert dispatch.job_ids == []


def test_the_url_source_is_disabled_without_an_allowlist(tmp_path: Path) -> None:
    settings = service_settings(tmp_path, url_allowed_hosts="")
    res = client_for(settings).post(
        "/v2/analyses",
        headers=AUTH,
        json={"cache_scope": SCOPE, "url": "https://media.example.com/a"},
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "source_disabled"


def test_an_upload_is_usable_only_in_its_own_scope_and_only_once(settings, tmp_path: Path) -> None:
    client = client_for(settings)
    upload_id = _upload(client, tmp_path)
    other = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": "scope_b", "upload_id": upload_id}
    )
    assert other.status_code == 404 and other.json()["error"]["code"] == "upload_not_found"
    first = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
    )
    assert first.status_code == 202
    again = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
    )
    assert again.status_code == 404


def test_jobs_are_visible_only_in_their_scope(settings, tmp_path: Path) -> None:
    client = client_for(settings)
    upload_id = _upload(client, tmp_path)
    job_id = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
    ).json()["job_id"]
    assert client.get(f"/v2/jobs/{job_id}?cache_scope={SCOPE}", headers=AUTH).status_code == 200
    for query in ("?cache_scope=scope_b", "", "?cache_scope="):
        res = client.get(f"/v2/jobs/{job_id}{query}", headers=AUTH)
        assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    assert client.get("/v2/jobs/job_unknown?cache_scope=scope_a", headers=AUTH).status_code == 404


def test_a_queue_outage_starts_nothing_and_frees_the_upload(settings, tmp_path: Path) -> None:
    def down(job_id: str) -> None:
        raise ConnectionError("redis down")

    client = client_for(settings, down)
    upload_id = _upload(client, tmp_path)
    res = client.post(
        "/v2/analyses", headers=AUTH, json={"cache_scope": SCOPE, "upload_id": upload_id}
    )
    assert res.status_code == 503 and res.json()["error"]["code"] == "queue_unavailable"
    store = Store(settings.service_db_path)
    assert store.claim_upload(upload_id, SCOPE) is not None
