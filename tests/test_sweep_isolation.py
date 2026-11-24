"""Each sweep step runs on its own (#67 review): one that raises is logged and every other step
still runs, so a failing global purge never keeps expired audio or scoped entries, and a failing
scoped or upload purge never keeps global ones. Time is injected; nothing leaves the host."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List

import pytest

from app.service import sweep
from app.service.store import Store
from app.service.uploads import upload_path

from .test_global_retention import DAY, Clock, present, put
from .test_scoped_retention import _upload, kept, purged, put_mine, sweep_settings

MODULE_STEPS = ("expire_stale_jobs", "remove_orphan_work_dirs", "purge_expired_uploads")
STORE_STEPS = ("purge_global_references", "purge_unused_analyses", "purge_scoped")
STEPS = MODULE_STEPS + STORE_STEPS
SECRET = "https://media.example.com/a.wav?X-Signature=s3cr3t"


@pytest.mark.parametrize("broken", STEPS)
def test_a_failing_step_never_skips_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, broken: str
) -> None:
    settings = sweep_settings(tmp_path)
    store = Store(settings.service_db_path)
    called: List[str] = []

    def recorder(name: str) -> Any:
        def step(*args: Any, **kwargs: Any) -> None:
            called.append(name)
            if name == broken:
                raise RuntimeError(SECRET)

        return step

    for name in MODULE_STEPS:
        monkeypatch.setattr(sweep, name, recorder(name))
    for name in STORE_STEPS:
        monkeypatch.setattr(store, name, recorder(name))
    with caplog.at_level("ERROR", logger="spotdown.service.sweep"):
        failed = sweep.sweep(settings, store)
    assert called == list(STEPS)  # every step ran, in order, the broken one included
    assert failed == [broken]
    assert f"sweep step {broken} failed: RuntimeError" in caplog.text
    assert "s3cr3t" not in caplog.text  # the type only, never the message


def _world(tmp_path: Path) -> Any:
    """An expired upload, a global and a scoped analysis both unread for 100 days."""
    settings = sweep_settings(tmp_path)
    clock = Clock(start=time.time() - 100 * DAY)
    store = Store(settings.service_db_path, clock=clock)
    put(store, clock, "g")
    put_mine(store, clock, "s")
    _upload(settings, store, "up_old", 1)
    return settings, store


def _break(store: Store, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk I/O error")

    if name in MODULE_STEPS:
        monkeypatch.setattr(sweep, name, broken)
    else:
        monkeypatch.setattr(store, name, broken)


def _upload_gone(settings: Any, store: Store) -> bool:
    return store.get_upload("up_old") is None and not upload_path(settings, "up_old").exists()


@pytest.mark.parametrize("broken", ["purge_global_references", "purge_unused_analyses"])
def test_a_failing_global_purge_still_purges_scoped_entries_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    settings, store = _world(tmp_path)
    _break(store, monkeypatch, broken)
    assert sweep.sweep(settings, store) == [broken]
    assert purged(store, "s")
    assert _upload_gone(settings, store)


def test_a_failing_scoped_purge_still_purges_global_entries_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, store = _world(tmp_path)
    _break(store, monkeypatch, "purge_scoped")
    assert sweep.sweep(settings, store) == ["purge_scoped"]
    assert present(store, "g")["analyses"] == 0
    assert kept(store, "s")  # only the broken step's own work is left
    assert _upload_gone(settings, store)


@pytest.mark.parametrize("broken", ["expire_stale_jobs", "remove_orphan_work_dirs"])
def test_a_failing_job_step_still_purges_uploads_and_analyses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    settings, store = _world(tmp_path)
    _break(store, monkeypatch, broken)
    assert sweep.sweep(settings, store) == [broken]
    assert _upload_gone(settings, store)
    assert present(store, "g")["analyses"] == 0 and purged(store, "s")


def test_a_failing_upload_purge_still_purges_global_and_scoped_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, store = _world(tmp_path)
    _break(store, monkeypatch, "purge_expired_uploads")
    assert sweep.sweep(settings, store) == ["purge_expired_uploads"]
    assert present(store, "g")["analyses"] == 0 and purged(store, "s")
    assert store.get_upload("up_old") is not None  # only the broken step's own work is left
