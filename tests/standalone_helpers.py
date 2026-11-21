"""Helpers for standalone-mode tests: a client on an allowed host that has signed in through the
real ``POST /login`` (ADR 0003). Each test uses its own temporary ``data_dir``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings

BASE = "http://localhost:8000"
UI_HEADERS = {"X-Spotdown-UI": "1", "Origin": BASE}
LOGIN_HEADERS = {**UI_HEADERS, "Content-Type": "application/json"}


def standalone_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict = {"data_dir": tmp_path / "data", "redis_url": "redis://127.0.0.1:1/0"}
    values.update(overrides)
    return Settings(**values)


def generated_token(settings: Settings) -> str:
    return settings.standalone_token_path.read_text(encoding="ascii").strip()


def session_cookie(client: TestClient, token: str) -> str:
    """``name=value`` of the cookie a real ``POST /login`` sets."""
    res = client.post("/login", json={"token": token}, headers=LOGIN_HEADERS)
    assert res.status_code == 204, res.text
    return res.headers["set-cookie"].split(";", 1)[0]


def signed_in(app: FastAPI, token: str, base_url: str = BASE) -> TestClient:
    """A client that sends the session cookie and the UI headers on every request."""
    client = TestClient(app, base_url=base_url, headers=UI_HEADERS)
    client.headers["Cookie"] = session_cookie(client, token)
    return client


class _Job:
    id = "job-1"


class SpyQueue:
    def __init__(self) -> None:
        self.jobs: list = []

    def enqueue(self, *args: Any, **kwargs: Any) -> _Job:
        self.jobs.append((args, kwargs))
        return _Job()


class _NoRedis:
    def ping(self) -> bool:
        raise ConnectionError("no redis in tests")


def _no_such_job(*args: Any, **kwargs: Any) -> Any:
    from rq.exceptions import NoSuchJobError

    raise NoSuchJobError("no redis in tests")


class Standalone:
    """A standalone app on a temporary ``data_dir`` with spies for the queue, Spotify and the
    analyser (nothing real is called), and its generated token."""

    def __init__(self, tmp_path: Path, monkeypatch: Any, **overrides: Any) -> None:
        from app import analyze, db, main, spotify, standalone

        self.settings = standalone_settings(tmp_path, **overrides)
        for module in (db, standalone):
            monkeypatch.setattr(module, "get_settings", lambda: self.settings)
        monkeypatch.setattr(main, "get_redis", lambda: _NoRedis())
        monkeypatch.setattr("rq.job.Job.fetch", _no_such_job)
        self.queue = SpyQueue()
        monkeypatch.setattr(standalone, "get_queue", lambda: self.queue)
        self.spotify_calls: list = []
        self.analyses: list = []
        monkeypatch.setattr(spotify, "resolve", lambda url: self.spotify_calls.append(url) or [])
        monkeypatch.setattr(
            analyze, "analyze_track", lambda *a, **k: self.analyses.append(a) or {"ok": 1}
        )
        self.app = main.create_app(self.settings)

    @property
    def token(self) -> str:
        path = self.settings.standalone_access_token_file or self.settings.standalone_token_path
        return path.read_text(encoding="utf-8").strip()

    def anon(self, base_url: str = BASE) -> TestClient:
        return TestClient(self.app, base_url=base_url)

    def signed(self, base_url: str = BASE) -> TestClient:
        return signed_in(self.app, self.token, base_url)
