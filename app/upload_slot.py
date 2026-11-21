"""The one slot of the standalone local-file route (ADR 0002, Decision 3 and 4).

At most one local-file request is in flight per installation, from its first body byte to the
deletion of its file. The slot is an exclusive, non-blocking ``fcntl.flock`` on a lock file in the
standalone upload directory, so it holds across processes.

- The lock file is opened with ``O_CREAT`` only (never ``O_TRUNC`` or ``O_EXCL``), is never
  deleted, replaced or renamed over, and the sweep skips it by its exact name, so every holder
  locks the same inode.
- The sweep (everything in the directory except the lock file) runs only while holding the slot:
  with one slot, anything there while nobody holds the lock is a leftover. ``startup_sweep``
  sweeps only if it gets the lock without blocking.

POSIX only: ``fcntl`` is imported inside a guard. Without it ``AVAILABLE`` is False and the
route is not registered; there is no fallback lock.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

try:
    import fcntl
except ImportError:  # Windows outside a container
    fcntl = None  # type: ignore[assignment]

AVAILABLE = fcntl is not None
LOCK_NAME = ".slot.lock"

log = logging.getLogger("spotdown.upload_slot")

# Slots whose analysis child could not be shown to have exited: their descriptors are kept open
# for the life of the process, so the lock is never released (a restart frees it).
_NEVER_RELEASED: List["Slot"] = []


class Slot:
    """A held slot. ``release`` once, after the request's files are deleted."""

    def __init__(self, directory: Path, fd: int) -> None:
        self.directory = directory
        self._fd: Optional[int] = fd

    @property
    def held(self) -> bool:
        return self._fd is not None

    def sweep(self) -> None:
        """Deletes everything in the directory except the lock file (only while held). An entry
        that cannot be deleted raises OSError (after the others are deleted); none is skipped
        silently."""
        if self._fd is None:
            raise RuntimeError("the sweep runs only while holding the slot")
        failed: Optional[OSError] = None
        for entry in os.scandir(self.directory):
            if entry.name == LOCK_NAME:
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                failed = exc
        if failed is not None:
            raise failed

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            assert fcntl is not None
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def keep_forever(self) -> None:
        """Never release this slot in this process (fail closed)."""
        _NEVER_RELEASED.append(self)


def try_acquire(directory: Path) -> Optional[Slot]:
    """The slot if it is free now, else None. Never blocks."""
    if fcntl is None:
        raise RuntimeError("the upload slot needs fcntl")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(directory / LOCK_NAME, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return Slot(directory, fd)


def startup_sweep(directory: Path) -> bool:
    """Sweeps leftovers if the slot is free; skips (False) while another request holds it."""
    slot = try_acquire(directory)
    if slot is None:
        log.info("local-file slot in use at startup; leftover sweep skipped")
        return False
    try:
        slot.sweep()
    finally:
        slot.release()
    return True
