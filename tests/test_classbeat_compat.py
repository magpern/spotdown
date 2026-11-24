"""classbeat compatibility (spotdown#79 acceptance 9): the image still runs unchanged under
classbeat's compose overlay (classbeat 757a782, ``compose.prod.spotdown.yaml``). The api starts
from classbeat's environment alone (with the now-ignored ``SERVICE_MODE`` and ``SERVE_UI``, no UI
key and no Spotify credentials); the worker needs no key; the queue name and the enqueued job path
are the ones classbeat's ``rq worker`` command and already queued jobs use. No network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.queue import QUEUE_NAME
from app.service.dispatch import rq_dispatcher

from .service_helpers import AUTH, TEST_KEY, RecordingDispatch, wav_bytes

ROOT = Path(__file__).resolve().parent.parent
# classbeat's worker command and job path, as classbeat 757a782 runs them.
CLASSBEAT_WORKER = ["rq", "worker", "-u", "redis://spotdown-redis:6379/0", "spotdown"]
JOB_PATH = "app.service.worker.run_job"
# Settings classbeat never sets: none may be needed for the api or the worker to start.
NOT_SET = [
    "UI_SERVICE_KEY_FILE",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET_FILE",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTDOWN_UI",
]


def _classbeat_env(tmp_path: Path, ytdlp: str, api: bool) -> Dict[str, str]:
    env = {
        "SERVICE_MODE": "true",
        "SERVE_UI": "false",
        "DATA_DIR": str(tmp_path / "data"),
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "URL_ALLOWED_HOSTS": "",
        "ALLOW_YTDLP_SOURCING": ytdlp,
    }
    if api:
        key = tmp_path / "spotdown_service_key"
        key.write_text(TEST_KEY + "\n", encoding="utf-8")
        env["SERVICE_KEY_FILE"] = str(key)
    return env


def _apply(monkeypatch, env: Dict[str, str]) -> None:
    for name in NOT_SET + ["SERVICE_KEY_FILE"]:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("ytdlp", ["true", "false"])
def test_the_api_starts_from_classbeats_environment(tmp_path: Path, monkeypatch, ytdlp) -> None:
    _apply(monkeypatch, _classbeat_env(tmp_path, ytdlp, api=True))
    monkeypatch.chdir(tmp_path)  # no .env of the checkout
    settings = Settings()
    assert settings.allow_ytdlp_sourcing is (ytdlp == "true")
    before = set(tmp_path.iterdir())
    dispatch = RecordingDispatch()
    client = TestClient(create_app(settings, dispatch))
    # Only DATA_DIR is written (a read-only root with /data and /tmp writable).
    assert set(tmp_path.iterdir()) - before == {tmp_path / "data"}
    res = client.get("/healthz")
    assert res.status_code == 200 and res.json()["ok"] is True
    assert client.get("/v2/jobs/job_1").status_code == 401
    # classbeat's flow: PUT /v2/uploads, POST /v2/analyses, GET /v2/jobs/{id}.
    octet = {**AUTH, "Content-Type": "application/octet-stream", "Idempotency-Key": "u-1"}
    up = client.put("/v2/uploads?cache_scope=cb", headers=octet, content=wav_bytes(tmp_path, 2))
    assert up.status_code == 201, up.text
    body = {"cache_scope": "cb", "client_ref": "t-1", "upload_id": up.json()["upload_id"]}
    res = client.post("/v2/analyses", headers={**AUTH, "Idempotency-Key": "a-1"}, json=body)
    assert res.status_code == 202 and res.json()["status"] == "queued"
    job_id = res.json()["job_id"]
    assert dispatch.job_ids == [job_id]
    job = client.get(f"/v2/jobs/{job_id}?cache_scope=cb", headers=AUTH)
    assert job.status_code == 200 and job.json()["status"] == "queued"
    missing = client.get("/v2/jobs/job_nope?cache_scope=cb", headers=AUTH)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_the_queue_and_the_job_path_are_classbeats(tmp_path: Path, monkeypatch) -> None:
    assert QUEUE_NAME == CLASSBEAT_WORKER[-1]
    enqueued: List[Any] = []

    class Queue:
        name = QUEUE_NAME

        def enqueue(self, func: str, *args: Any, **kwargs: Any) -> None:
            enqueued.append((self.name, func, args, kwargs["job_id"]))

    monkeypatch.setattr("app.queue.get_queue", lambda: Queue())
    rq_dispatcher(Settings(data_dir=tmp_path))("job_1")
    assert enqueued == [("spotdown", JOB_PATH, ("job_1",), "job_1")]


def test_the_worker_imports_its_job_and_runs_its_healthcheck_without_a_key(tmp_path) -> None:
    """In a fresh process with classbeat's worker environment (no key file): rq resolves the job
    path, and ``python -m app.healthcheck worker`` answers (1: no Redis here), never a crash."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_SET and k != "SERVICE_KEY_FILE"}
    env.update(_classbeat_env(tmp_path, "true", api=False))
    script = (
        "from rq.utils import import_attribute\n"
        f"assert callable(import_attribute({JOB_PATH!r}))\n"
        "import app.main, app.healthcheck\n"
        "print('ok')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stderr[-2000:]
    check = [sys.executable, "-m", "app.healthcheck", "worker"]
    probe = subprocess.run(check, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert probe.returncode == 1 and probe.stdout == "", probe.stderr[-2000:]
