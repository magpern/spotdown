"""Drive token creation (spotdown_ui/token_store.py) step by step: each of its file operations goes
through a proxy of ``os`` that can pause the calling worker before the operation, fail it, or
record it. Workers are threads; a step boundary is forced by holding a worker at its next hook
until the test releases it."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from spotdown_ui import token_store

HOOKED = ("open", "write", "fsync", "link", "unlink")


class OsProxy:
    """Forwards to the real ``os``; ``before(op, args)`` runs before each hooked operation and
    may raise to fail it. ``calls`` records ``(worker, op, args)``."""

    def __init__(self, before: Optional[Callable[[str, tuple], None]] = None) -> None:
        self.before = before or (lambda op, args: None)
        self.calls: List[tuple] = []
        self.hidden: set = set()
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        if name in self.hidden:
            raise AttributeError(name)
        real = getattr(os, name)
        if name not in HOOKED:
            return real

        def hooked(*args: Any) -> Any:
            with self._lock:
                self.calls.append((threading.current_thread().name, name, args))
            self.before(name, args)
            return real(*args)

        return hooked


class Scheduler:
    """Each controlled worker blocks at every hook until it is granted one step."""

    def __init__(self, ops: Optional[set] = None) -> None:
        self.ops = ops  # the ops that are step boundaries (None: every hooked op)
        self.cv = threading.Condition()
        self.waiting: Dict[str, str] = {}  # worker -> the op it waits before
        self.granted: set = set()
        self.free: set = set()
        self.done: set = set()
        self.results: Dict[str, Any] = {}
        self.threads: Dict[str, threading.Thread] = {}

    def hook(self, op: str, args: tuple = ()) -> None:
        me = threading.current_thread().name
        with self.cv:
            if me not in self.threads or me in self.free:
                return
            if self.ops is not None and op not in self.ops:
                return
            self.waiting[me] = op
            self.cv.notify_all()
            assert self.cv.wait_for(lambda: me in self.granted or me in self.free, 10)
            self.granted.discard(me)
            self.waiting.pop(me, None)

    def start(self, name: str, fn: Callable[[], Any]) -> None:
        def run() -> None:
            try:
                self.results[name] = fn()
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                self.results[name] = exc
            finally:
                with self.cv:
                    self.done.add(name)
                    self.cv.notify_all()

        thread = threading.Thread(target=run, name=name, daemon=True)
        self.threads[name] = thread
        thread.start()
        self._settle(name)

    def _settle(self, name: str) -> None:
        with self.cv:
            assert self.cv.wait_for(lambda: name in self.waiting or name in self.done, 10)

    def step(self, name: str) -> Optional[str]:
        """Let ``name`` make one operation; return the op it now waits before (None: done)."""
        with self.cv:
            if name in self.done:
                return None
            assert name in self.waiting, name
            self.granted.add(name)
            self.waiting.pop(name)
            self.cv.notify_all()
        self._settle(name)
        with self.cv:
            return self.waiting.get(name)

    def run_until(self, name: str, op: str, nth: int = 1) -> None:
        """Step ``name`` until it waits before its ``nth`` ``op`` (counting the current one)."""
        seen = 0
        current = self.waiting.get(name)
        while current is not None:
            if current == op:
                seen += 1
                if seen == nth:
                    return
            current = self.step(name)
        raise AssertionError(f"{name} finished before its {op} #{nth}")

    def release_all(self) -> Dict[str, Any]:
        with self.cv:
            self.free |= set(self.threads)
            self.cv.notify_all()
        for thread in self.threads.values():
            thread.join(10)
            assert not thread.is_alive()
        return self.results


def install(monkeypatch, sched: Scheduler, fail: Optional[Callable[[str, tuple], None]] = None):
    """Route token_store's file operations (and its reads) through ``sched``'s hooks."""

    def before(op: str, args: tuple) -> None:
        sched.hook(op, args)
        if fail is not None:
            fail(op, args)

    proxy = OsProxy(before)
    real_read = token_store._read

    def read(path: Path) -> Optional[bytes]:
        sched.hook("read", (path,))
        return real_read(path)

    monkeypatch.setattr(token_store, "os", proxy)
    monkeypatch.setattr(token_store, "_read", read)
    monkeypatch.setattr(token_store, "_sleep", lambda s: threading.Event().wait(0.001))
    return proxy


def link_unsupported(op: str, args: tuple) -> None:
    """Hard links unsupported (some Docker Desktop bind mounts): force the fallback."""
    if op == "link":
        raise OSError(errno.EPERM, "Operation not permitted")


def tmp_files(directory: Path) -> List[Path]:
    return sorted(directory.glob("ui_token.tmp-*"))
