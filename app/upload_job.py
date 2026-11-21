"""One sandboxed analysis of the standalone local-file route, cancellable without losing its child.

The PR #53 PASS (non-blocking 7), spotdown#49: the slot is never released while the sandbox
child runs, and a cancelled request kills the child promptly, not after ``wall_s``.

- ``run`` awaits ``sandbox.run_analysis`` in a worker thread with ``abandon_on_cancel=True``,
  so a cancellation (client disconnect, shutdown) reaches the handler at once. The thread keeps
  running and sets ``done`` in its own ``finally``, after ``run_analysis`` (which reaps the child)
  has returned or raised.
- ``stop`` (from the handler's ``finally``, on a thread of its own) kills the child's process
  group if ``done`` is not set, never when the child's ``returncode`` is already set, then waits
  for ``done`` up to ``kill_group``'s grace plus 5 s. False means the child could not be shown to
  have exited: the caller then deletes nothing and keeps the slot.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import anyio.to_thread

from .service import sandbox

WAIT_BOUND_S = sandbox.KILL_GRACE_S + 5.0


class Cancelled(Exception):
    """Raised in the worker thread by the start hook of a job already stopped."""


class Job:
    def __init__(self, audio: Path, media: str, work: Path, limits: sandbox.Limits) -> None:
        self._args = (audio, media, work, limits)
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._stopping = False
        self.started = False  # set by the worker thread, under the lock, before the child
        self.done = threading.Event()
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[BaseException] = None

    def _on_start(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            if self._stopping:  # stopped before the child existed: run_analysis kills it
                raise Cancelled()

    def _work(self) -> None:
        with self._lock:
            if self._stopping:  # stopped before this thread began: no child is ever started
                self.done.set()
                return
            self.started = True
        try:
            self.result = sandbox.run_analysis(*self._args, on_start=self._on_start)
        except BaseException as exc:  # noqa: BLE001 - handed to the request, never swallowed
            self.error = exc
        finally:
            self.done.set()

    async def run(self) -> Dict[str, Any]:
        """The child's result, or its JobFailure. A cancellation leaves the thread running."""
        await anyio.to_thread.run_sync(self._work, abandon_on_cancel=True)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def wait_done(self, timeout: float) -> bool:
        return self.done.wait(timeout)

    def stop(self) -> bool:
        """Kills the child if it may still run, then waits for the thread. True once it is done.

        Blocking: the route calls it on its own finishing thread, never on the event loop."""
        with self._lock:
            self._stopping = True
            started, proc = self.started, self._proc
        # Not started: the thread either never runs ``_work`` (anyio drops a call cancelled
        # before it began) or sees ``_stopping`` and starts nothing.
        if not started or self.done.is_set():
            return True
        if proc is not None and proc.returncode is None:  # never signal a reaped group id
            sandbox.kill_group(proc)
        return self.wait_done(WAIT_BOUND_S)
