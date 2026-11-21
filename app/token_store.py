"""The standalone access token: owner-supplied, or generated once (ADR 0003 item 2).

Creating the generated token is a small state machine with one rule: no process ever deletes,
replaces or truncates a file another process may be using. Each process writes its own temporary
file and links it into place (atomic, never replacing); only where hard links are unsupported
does it fall back to an ``O_EXCL`` create of the final file. There is no sweep and no lock; a
process unlinks only its own temporary file. A token file that is not valid is never rewritten:
the app answers ``503`` and re-reads it at most once a second until it becomes valid.

The token is never printed, logged or returned; the console line names only the file.
"""

from __future__ import annotations

import errno
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

from .session_policy import owner_token_problem, parse_generated_token

FINAL_NAME = "standalone_token"
REREAD_INTERVAL_S = 1.0
CONSTRUCTION_WAIT_S = 2.0
CONSTRUCTION_POLL_S = 0.05
_READ_LIMIT = 4096  # a larger file is never a valid token: it is read one byte past this

# Seams for the tests (time is patched, never slept for real in a test that forces a race).
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def _console(line: str) -> None:
    print(f"spotdown: {line}", flush=True)


def _nofollow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)  # Windows has none


def _read(path: Path) -> Optional[bytes]:
    """The file's bytes, or ``None`` if it does not exist. Follows symlinks (ADR 0003: the final
    file is read without ``O_NOFOLLOW``; a planted symlink's target is almost never valid)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(_READ_LIMIT + 1)
    except FileNotFoundError:
        return None
    except OSError:
        return b""  # present but unreadable (a directory, no permission): never valid


def _fsync_dir(directory: Path) -> None:
    """Best effort: skipped where a directory cannot be opened (Windows); any ``OSError`` (for
    example ``EINVAL`` on FUSE or 9p bind mounts) is ignored."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_final(final: Path) -> tuple[str, Optional[str]]:
    """Step 1: ``("valid", token)``, ``("absent", None)`` or ``("bad", None)``."""
    raw = _read(final)
    if raw is None:
        return "absent", None
    token = parse_generated_token(raw)
    return ("valid", token) if token else ("bad", None)


def _wait_valid(final: Path) -> Optional[str]:
    """A short or malformed final file may be a fallback winner still writing: re-read every
    50 ms for up to 2 s (construction only)."""
    deadline = _monotonic() + CONSTRUCTION_WAIT_S
    while True:
        state, token = _read_final(final)
        if state == "valid":
            return token
        if _monotonic() >= deadline:
            return None
        _sleep(CONSTRUCTION_POLL_S)


def _step1(final: Path) -> tuple[str, Optional[str]]:
    state, token = _read_final(final)
    if state == "bad":
        token = _wait_valid(final)
        state = "valid" if token else "bad"
    return state, token


def _fallback(final: Path, data: bytes, token: str) -> Optional[str]:
    """Step 5, only where linking is unsupported: ``O_EXCL`` create of the final file, one
    ``write``, ``fsync``. A failed write leaves a bad final file (503 until it is fixed)."""
    try:
        fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow(), 0o600)
    except FileExistsError:
        state, other = _step1(final)
        return other if state == "valid" else None
    except OSError:
        return None
    try:
        if os.write(fd, data) != len(data):
            return None
        os.fsync(fd)
    except OSError:
        return None
    finally:
        os.close(fd)
    _fsync_dir(final.parent)
    return token


def create_or_read(final: Path) -> Optional[str]:
    """The generated token at ``final``: read it, or create it once. ``None`` means no valid
    token (the caller answers 503)."""
    state, token = _step1(final)
    if state != "absent":
        return token
    token = secrets.token_hex(32)
    data = (token + "\n").encode("ascii")
    tmp = final.with_name(f"{FINAL_NAME}.tmp-{os.getpid()}-{secrets.token_hex(8)}")
    created = False
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow(), 0o600)
        except OSError:
            return None  # the directory is not writable, or a symlink sits at our own name
        created = True
        try:
            if os.write(fd, data) != len(data):
                raise OSError(errno.EIO, "short write")
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, final)
        except OSError as exc:
            state, other = _step1(final)  # back to step 1, once
            if state != "absent":
                return other
            if exc.errno == errno.EEXIST:
                return None  # it existed a moment ago and is gone (deleted from outside)
            return _fallback(final, data, token)
        _fsync_dir(final.parent)
        return token
    except OSError:  # writing our own temporary file failed (ENOSPC, EIO): step 1 once more
        state, other = _step1(final)
        return other if state == "valid" else None
    finally:
        if created:
            try:
                os.unlink(tmp)  # only this process's own temporary file
            except OSError:
                pass


class TokenSource:
    """The token state of one app: read or created at construction, then, while there is no
    valid token, re-read (never written) at most once a second."""

    def __init__(self, final: Path, owner: bool) -> None:
        self.path = final
        self.owner = owner
        self._token: Optional[str] = None
        self._last_read = 0.0
        if owner:
            self._token = self._read_owner(report=True)
        else:
            self._token = create_or_read(final)
            if self._token is None:
                _console(
                    f"the standalone token file {final} is missing, empty, short or malformed; "
                    "delete it and restart to generate a new token"
                )
        self._last_read = _monotonic()
        if self._token is not None:
            _console(f"standalone access token file: {final} (open /login and paste it)")

    def _read_owner(self, report: bool) -> Optional[str]:
        raw = _read(self.path)
        if not raw:
            if report:
                _console(
                    f"STANDALONE_ACCESS_TOKEN_FILE {self.path} is missing, unreadable or empty"
                )
            return None
        if len(raw) > _READ_LIMIT:  # never truncated into a valid token: trailing junk counts
            token, rule = None, "length"
        else:
            token, rule = owner_token_problem(raw)
        if token is None and report:
            _console(
                f"STANDALONE_ACCESS_TOKEN_FILE {self.path} breaks the owner-token rule: {rule} "
                "(32 to 512 characters, printable ASCII without '\"' or '\\')"
            )
        return token

    def current(self) -> Optional[str]:
        if self._token is not None:
            return self._token
        now = _monotonic()
        if now - self._last_read >= REREAD_INTERVAL_S:
            self._last_read = now
            if self.owner:
                self._token = self._read_owner(report=False)
            else:
                self._token = _read_final(self.path)[1]
            if self._token is not None:
                _console(f"standalone access token file {self.path} is now valid")
        return self._token

    def message(self) -> str:
        """The 503 detail: the file, never the token."""
        if self.owner:
            return f"The standalone access token file {self.path} is not usable; fix it."
        return (
            f"The standalone token file {self.path} is not usable; delete it and restart "
            "spotdown to generate a new token."
        )
