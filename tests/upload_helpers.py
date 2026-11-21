"""Helpers for the standalone local-file route tests (ADR 0002): settings, the real app (behind
ADR 0003's auth middleware) signed in through the real ``POST /login``, the headers the page
sends, and a fake sandbox. No network, no real account."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest
from fastapi.testclient import TestClient

from app import db, standalone, upload_slot
from app.config import Settings
from app.main import create_app
from app.service import sandbox
from app.upload_route import PATH

from .service_helpers import fake_analyser_result
from .standalone_helpers import generated_token

BASE = "http://localhost:8000"
ORIGIN = "http://localhost:8000"
GOOD = {"Content-Type": "application/octet-stream", "X-Spotdown-UI": "1", "Origin": ORIGIN}
LOCK = upload_slot.LOCK_NAME
__all__ = ["BASE", "GOOD", "LOCK", "ORIGIN", "PATH"]


def standalone_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: Dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "redis_url": "redis://127.0.0.1:1/0",
        "max_upload_bytes": 2_000_000,
        "analysis_wall_timeout_s": 120,
        "analysis_max_cpu_s": 60,
        "analysis_max_decoded_s": 60.0,
    }
    values.update(overrides)
    return Settings(**values)


def with_cookie(app: Any, cookie: str) -> Callable:
    """The app as a signed-in browser reaches it: every request carries the session cookie that
    a real ``POST /login`` set. The real middleware still checks it."""
    raw = (b"cookie", cookie.encode("latin-1"))

    async def browser(scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope = {**scope, "headers": [*scope.get("headers", []), raw]}
        await app(scope, receive, send)

    return browser


def make_app(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, session: bool = True, base: str = BASE
) -> Any:
    """``create_app`` (the auth middleware included), and, with ``session``, signed in through
    the real ``POST /login`` from ``base``."""
    for module in (db, standalone):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    app = create_app(settings)
    db.init_db()
    if not session:
        return app
    login = TestClient(app, base_url=base)
    res = login.post(
        "/login",
        json={"token": generated_token(settings)},
        headers={"X-Spotdown-UI": "1", "Origin": base, "Content-Type": "application/json"},
    )
    assert res.status_code == 204, res.text
    return with_cookie(app, res.headers["set-cookie"].split(";", 1)[0])


def left(settings: Settings) -> List[str]:
    """Everything in the standalone upload directory (the lock file included)."""
    return sorted(os.listdir(settings.standalone_uploads_dir))


def fake_sandbox(monkeypatch: pytest.MonkeyPatch, calls: List[Path] | None = None) -> None:
    """``run_analysis`` returns a canned result without a child process."""

    def run(audio: Path, media: str, work: Path, limits: Any, on_start: Any = None) -> Any:
        if calls is not None:
            calls.append(audio)
        assert work.is_dir() and audio.is_file()
        return fake_analyser_result()

    monkeypatch.setattr(sandbox, "run_analysis", run)


def release_kept_slots() -> None:
    """Frees the slots a fail-closed test deliberately kept."""
    while upload_slot._NEVER_RELEASED:
        upload_slot._NEVER_RELEASED.pop().release()
