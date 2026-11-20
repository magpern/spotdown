"""Recovery after a killed worker: stale running jobs fail, their audio and URL go, and a job
the sweep failed is never marked done afterwards (spotdown#16 review)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config import Settings
from app.service import analysis_doc, sweep
from app.service.dispatch import job_lease_s, job_timeout_s
from app.service.store import Store
from app.service.uploads import upload_path
from app.service.worker import execute_job

from .service_helpers import AUTH, SCOPE, client_for, fake_analyser_result, service_settings

URL = "https://media.example.com/a.wav?X-Signature=s3cr3t"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = service_settings(tmp_path)
    s.ensure_dirs()
    return s


@pytest.fixture()
def store(settings: Settings) -> Store:
    return Store(settings.service_db_path)


def killed_job(settings: Settings, store: Store, job_id: str, age_s: float) -> Path:
    """A job a worker started ``age_s`` ago and never finished: running, its URL and upload
    still set, fetched audio in its work directory."""
    upload_id = "up_" + job_id
    upload_path(settings, upload_id).write_bytes(b"RIFF")
    store.add_upload(upload_id, SCOPE, "a" * 64, 4, "wav", 3600)
    assert store.claim_upload(upload_id, SCOPE) is not None
    store.add_job(
        {
            "job_id": job_id,
            "cache_scope": SCOPE,
            "source_kind": "url",
            "source_ref": "sha256:x",
            "fetch_url": URL,
            "upload_id": upload_id,
        }
    )
    assert store.start_job(job_id)
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE job_id = ?", (time.time() - age_s, job_id)
        )
    work = settings.work_dir / job_id
    work.mkdir(parents=True)
    (work / "source").write_bytes(b"fetched audio")
    return work


def test_a_job_past_its_lease_fails_and_leaves_nothing(settings, store) -> None:
    stale = killed_job(settings, store, "job_stale", job_lease_s(settings) + 5)
    fresh = killed_job(settings, store, "job_fresh", 10)
    sweep.sweep(settings, store)

    job = store.get_job("job_stale")
    assert job is not None and job["status"] == "failed"
    assert (job["outcome"], job["failure_kind"]) == ("processing_failed", "timeout")
    assert job["fetch_url"] is None
    assert not stale.exists()
    assert not upload_path(settings, "up_job_stale").exists()
    assert store.get_upload("up_job_stale") is None
    # a job still within its lease keeps running, with its directory and upload
    kept = store.get_job("job_fresh")
    assert kept is not None and kept["status"] == "running" and kept["fetch_url"] == URL
    assert (fresh / "source").exists() and upload_path(settings, "up_job_fresh").exists()


def test_work_directories_of_jobs_that_are_not_running_are_removed(settings, store) -> None:
    (settings.work_dir / "job_gone").mkdir()
    (settings.work_dir / "job_gone" / "source").write_bytes(b"x")
    (settings.work_dir / "stray").write_bytes(b"x")
    running = killed_job(settings, store, "job_run", 1)
    sweep.remove_orphan_work_dirs(settings, store)
    assert sorted(p.name for p in settings.work_dir.iterdir()) == ["job_run"]
    assert (running / "source").exists()


def test_a_swept_job_is_never_marked_done_later(settings, store) -> None:
    killed_job(settings, store, "job_late", job_lease_s(settings) + 5)
    sweep.expire_stale_jobs(settings, store)
    result = fake_analyser_result()
    doc = analysis_doc.build(
        core=result["core"],
        probe=result["probe"],
        components=result["components"],
        sha256="a" * 64,
        source={"kind": "upload"},
        job_id="job_late",
        analysis_id="an_late",
        timings_s={},
        created_at="2026-11-20T00:00:00Z",
    )
    assert store.finish_job_done("job_late", "an_late", SCOPE, doc) is False
    assert store.get_analysis("an_late") is None
    store.finish_job_failed("job_late", "provider_error", None, None)
    job = store.get_job("job_late")
    assert job is not None and job["status"] == "failed" and job["outcome"] == "processing_failed"


def test_polling_a_job_whose_worker_was_killed_reports_it_failed(settings, store) -> None:
    client = client_for(settings)
    killed_job(settings, store, "job_poll", job_lease_s(settings) + 5)
    res = client.get(f"/v2/jobs/job_poll?cache_scope={SCOPE}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["status"] == "failed" and res.json()["failure_kind"] == "timeout"
    assert "s3cr3t" not in res.text


def test_the_worker_sweeps_before_its_job(settings, store) -> None:
    stale = killed_job(settings, store, "job_old", job_lease_s(settings) + 5)
    execute_job("job_missing", settings, store)  # no such job: only the sweep runs
    assert not stale.exists()
    job = store.get_job("job_old")
    assert job is not None and job["status"] == "failed"


def test_the_lease_is_longer_than_the_queue_timeout(settings) -> None:
    assert job_lease_s(settings) > job_timeout_s(settings)
    assert (
        job_timeout_s(settings) >= settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s
    )
