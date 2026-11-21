"""A cancelled local-file request kills its sandbox child promptly, and the slot is never released
while that child runs (spotdown#49, from the PR #53 PASS non-blocking 7).

The real ``sandbox.run_analysis`` runs with its real process group and rlimits; only the command
is swapped for a child that sleeps, so no analysis runs and nothing is decoded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import anyio
import httpx
import pytest

pytest.importorskip("fcntl")

from app import upload_job, upload_slot  # noqa: E402
from app.service import sandbox  # noqa: E402

from . import media_fixtures as mf  # noqa: E402
from .upload_helpers import (  # noqa: E402
    BASE,
    GOOD,
    LOCK,
    PATH,
    left,
    make_app,
    release_kept_slots,
    standalone_settings,
)

WAV = mf.wav(seconds=0.5)
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture()
def settings(tmp_path: Path):
    return standalone_settings(tmp_path, analysis_wall_timeout_s=60)


@pytest.fixture()
def procs(monkeypatch) -> Iterator[List[subprocess.Popen]]:
    """Every child the sandbox starts, running ``SLEEPER`` with the sandbox's own options."""
    started: List[subprocess.Popen] = []

    def popen(cmd: Any, **kwargs: Any) -> subprocess.Popen:
        proc = subprocess.Popen(SLEEPER, **kwargs)
        started.append(proc)
        return proc

    fake = SimpleNamespace(
        Popen=popen, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL
    )
    monkeypatch.setattr(sandbox, "subprocess", fake)
    yield started
    for proc in started:  # never leave a child behind, whatever the test did
        if proc.returncode is None:
            sandbox.kill_group(proc)
    release_kept_slots()


def _gone(proc: subprocess.Popen) -> bool:
    """The whole process group has exited and the child is reaped."""
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return proc.returncode is not None
    return False


async def _started(procs: List[subprocess.Popen]) -> subprocess.Popen:
    for _ in range(500):
        if procs:
            return procs[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no child started")


async def _cancelled(task: asyncio.Task) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def _slow_kill(monkeypatch, delay: float, log: List[Any]) -> None:
    """``kill_group`` slowed down; logs the child's ``returncode`` at each call (None: running)."""
    real = sandbox.kill_group

    def kill(proc: subprocess.Popen) -> None:
        log.append(proc.returncode)
        time.sleep(delay)
        real(proc)

    monkeypatch.setattr(sandbox, "kill_group", kill)


@pytest.mark.parametrize("how", ["cancel_scope", "task_cancel"])
def test_cancelling_kills_the_child_promptly(settings, monkeypatch, procs, how: str) -> None:
    """``cancel_scope`` is how anyio and Starlette cancel (a task group's scope): it reaches the
    handler at once only because the thread is awaited with ``abandon_on_cancel=True``."""
    app = make_app(settings, monkeypatch)
    timing: Dict[str, float] = {}

    async def go() -> None:
        async with _client(app) as client:
            if how == "task_cancel":
                task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
                await _started(procs)
                await asyncio.sleep(0.5)
                timing["cancel"] = time.monotonic()
                task.cancel()
                await _cancelled(task)
            else:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(lambda: client.post(PATH, headers=GOOD, content=WAV))
                    await _started(procs)
                    await asyncio.sleep(0.5)
                    timing["cancel"] = time.monotonic()
                    tg.cancel_scope.cancel()
            timing["back"] = time.monotonic()
            assert _gone(procs[0]) and procs[0].returncode == -9

    asyncio.run(go())
    assert timing["back"] - timing["cancel"] < 1.5  # well before wall_s (60 s)
    assert left(settings) == [LOCK]
    slot = upload_slot.try_acquire(settings.standalone_uploads_dir)
    assert slot is not None
    slot.release()


def test_a_disconnect_kills_the_child_too(settings, monkeypatch, procs) -> None:
    """A client that goes away mid-analysis cancels it (no task cancellation needed)."""
    app = make_app(settings, monkeypatch)
    messages: List[Dict[str, Any]] = []
    gone = asyncio.Event

    async def go() -> None:
        disconnect = gone()
        sent = []

        async def receive() -> Dict[str, Any]:
            if not sent:
                sent.append(1)
                return {"type": "http.request", "body": WAV, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: Dict[str, Any]) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": PATH,
            "raw_path": PATH.encode(),
            "query_string": b"",
            "root_path": "",
            "client": ("127.0.0.1", 1),
            "server": ("localhost", 8000),
            "headers": [(b"host", b"localhost:8000")]
            + [(k.lower().encode(), v.encode()) for k, v in GOOD.items()],
        }
        task = asyncio.create_task(app(scope, receive, send))
        proc = await _started(procs)
        await asyncio.sleep(0.3)
        t0 = time.monotonic()
        disconnect.set()
        await task
        assert time.monotonic() - t0 < 1.5 and _gone(proc)

    asyncio.run(go())
    assert left(settings) == [LOCK]


def test_the_slot_is_held_until_the_child_has_exited(settings, monkeypatch, procs) -> None:
    kills: List[Any] = []
    _slow_kill(monkeypatch, 1.0, kills)
    app = make_app(settings, monkeypatch)
    released: List[Any] = []
    real_release = upload_slot.Slot.release

    def release(self) -> None:
        if self.held and procs:
            released.append((procs[0].returncode, sorted(os.listdir(self.directory))))
        real_release(self)

    monkeypatch.setattr(upload_slot.Slot, "release", release)

    async def go() -> None:
        async with _client(app) as client:
            task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
            proc = await _started(procs)
            work = [p for p in settings.standalone_uploads_dir.iterdir() if p.is_dir()]
            assert len(work) == 1
            task.cancel()
            while not kills:
                await asyncio.sleep(0.01)
            # The kill and the wait are running: the slot and the work directory are still there.
            assert proc.returncode is None and work[0].is_dir()
            busy = await client.post(PATH, headers=GOOD, content=WAV)
            assert busy.status_code == 429
            await _cancelled(task)
            assert _gone(proc)

    asyncio.run(go())
    assert released == [(-9, [LOCK])]
    assert left(settings) == [LOCK]


def test_the_event_loop_answers_while_the_kill_and_wait_run(settings, monkeypatch, procs) -> None:
    kills: List[Any] = []
    _slow_kill(monkeypatch, 1.0, kills)
    app = make_app(settings, monkeypatch)

    async def go() -> None:
        async with _client(app) as client:
            task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
            await _started(procs)
            task.cancel()
            while not kills:
                await asyncio.sleep(0.01)
            t0 = time.monotonic()
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert time.monotonic() - t0 < 0.9 and not task.done()
            await _cancelled(task)

    asyncio.run(go())
    assert left(settings) == [LOCK]


def test_a_second_cancellation_neither_skips_the_kill_nor_frees_early(
    settings, monkeypatch, procs
) -> None:
    kills: List[Any] = []
    _slow_kill(monkeypatch, 0.5, kills)
    app = make_app(settings, monkeypatch)
    released: List[Any] = []
    real_release = upload_slot.Slot.release

    def release(self) -> None:
        if self.held and procs:
            released.append(procs[0].returncode)
        real_release(self)

    monkeypatch.setattr(upload_slot.Slot, "release", release)

    async def go() -> None:
        async with _client(app) as client:
            task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
            proc = await _started(procs)
            task.cancel()
            while not kills:
                await asyncio.sleep(0.01)
            task.cancel()  # again, during the kill
            await asyncio.sleep(0.1)
            task.cancel()  # and once more
            await _cancelled(task)
            assert _gone(proc)

    asyncio.run(go())
    # One kill of the running child (the request's); run_analysis's own reap-time call follows.
    assert kills[0] is None and kills.count(None) == 1 and released == [-9]
    assert left(settings) == [LOCK]


def test_no_signal_once_the_child_has_exited(settings, monkeypatch, procs) -> None:
    """The child has exited and been reaped but the thread is not done yet: no killpg."""
    app = make_app(settings, monkeypatch)
    real_run = sandbox.run_analysis
    after_child = threading.Event()
    let_go = threading.Event()

    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return real_run(*args, **kwargs)
        finally:
            after_child.set()
            let_go.wait(10)

    monkeypatch.setattr(sandbox, "run_analysis", run)
    monkeypatch.setattr(sys.modules[__name__], "SLEEPER", [sys.executable, "-c", "pass"])
    signals: List[Any] = []
    real_killpg = os.killpg

    async def go() -> None:
        async with _client(app) as client:
            task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
            while not after_child.is_set():
                await asyncio.sleep(0.01)
            assert procs[0].returncode is not None
            pid = procs[0].pid

            def spy(pgid: int, sig: int) -> None:
                if pgid == pid:
                    signals.append(sig)
                real_killpg(pgid, sig)

            monkeypatch.setattr(os, "killpg", spy)
            task.cancel()
            threading.Timer(0.5, let_go.set).start()
            await _cancelled(task)

    asyncio.run(go())
    assert signals == [] and _gone(procs[0])
    assert left(settings) == [LOCK]


def test_fail_closed_when_the_child_cannot_be_shown_to_exit(
    settings, monkeypatch, procs, caplog
) -> None:
    monkeypatch.setattr(upload_job.Job, "wait_done", lambda self, timeout: False)
    app = make_app(settings, monkeypatch)
    jobs: List[Any] = []
    real_init = upload_job.Job.__init__

    def init(self, *args: Any) -> None:
        real_init(self, *args)
        jobs.append(self)

    monkeypatch.setattr(upload_job.Job, "__init__", init)

    async def go() -> None:
        async with _client(app) as client:
            task = asyncio.create_task(client.post(PATH, headers=GOOD, content=WAV))
            await _started(procs)
            task.cancel()
            await _cancelled(task)

    with caplog.at_level(logging.ERROR, logger="spotdown.upload_route"):
        asyncio.run(go())
    assert "slot kept until restart" in caplog.text
    assert upload_slot.try_acquire(settings.standalone_uploads_dir) is None  # still held
    names = left(settings)
    assert len(names) == 3 and LOCK in names  # the file and the work directory are kept
    assert jobs and jobs[0].done.wait(10) and _gone(procs[0])  # the kill still happened
