"""``PUT /api/uploads``: a local file streamed to ``PUT /v2/uploads`` (ADR 0002 in the UI image,
ADR 0005).

In order, after the auth middleware (``Host``, session, ``Origin``, the exact
``application/octet-stream`` type and ``X-Spotdown-UI``), which reads no body byte:

1. a declared ``Content-Length`` over ``MAX_UPLOAD_BYTES`` (or not a number) is ``413``, before the
   slot and before any body byte;
2. the one slot: ``429`` with ``Retry-After`` while another upload runs, and the body is not read;
3. the body is streamed to the backend chunk by chunk, never written to disk. Past the cap it
   stops at once (``413``; no further byte is read); a body slower than ``UI_UPLOAD_READ_S`` in
   all is ``408``; a client that goes away is ``400``. The backend request is abandoned in each
   case, so the backend keeps nothing.

The UI keeps no file, no file name and no audio. The image runs one process, so the slot is held
in memory.
"""

from __future__ import annotations

import time
from typing import AsyncIterator, Callable, Optional

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse

from .api_policy import error_body
from .backend import BackendClient

RETRY_AFTER_S = 10
_monotonic: Callable[[], float] = time.monotonic  # a seam for the tests


def declared_too_large(value: Optional[str], cap: int) -> bool:
    """A declared length that is not a plain ASCII number, or is over the cap."""
    return value is not None and (not (value.isascii() and value.isdigit()) or int(value) > cap)


class Slot:
    """One upload at a time. Taking it never awaits, so two requests cannot both get it."""

    def __init__(self) -> None:
        self.busy = False

    def try_take(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def release(self) -> None:
        self.busy = False


class _Stopped(Exception):
    """Ends the streamed body; ``Reader.problem`` says why."""


class Reader:
    """The browser's body as chunks, under the byte cap and one deadline for the whole body."""

    def __init__(self, request: Request, cap: int, deadline_s: float) -> None:
        self.receive = request.receive
        self.cap = cap
        self.deadline_s = deadline_s
        self.read = 0
        self.problem: Optional[str] = None

    def _stop(self, problem: str) -> _Stopped:
        self.problem = problem
        return _Stopped(problem)

    async def chunks(self) -> AsyncIterator[bytes]:
        end = _monotonic() + self.deadline_s
        while True:
            left = end - _monotonic()
            if left <= 0:
                raise self._stop("deadline")
            try:
                with anyio.fail_after(left):
                    message = await self.receive()
            except TimeoutError:
                raise self._stop("deadline") from None
            if message["type"] != "http.request":
                raise self._stop("disconnect")
            chunk = message.get("body", b"")
            self.read += len(chunk)
            if self.read > self.cap:
                raise self._stop("too_large")
            if chunk:
                yield chunk
            if not message.get("more_body", False):
                return


_PROBLEMS = {
    "too_large": (413, "too_large", "the file is over the size limit"),
    "deadline": (408, "upload_timeout", "the upload took too long"),
    "disconnect": (400, "client_gone", "the client went away"),
}


def _answer(status: int, code: str, message: str, retry: bool = False) -> JSONResponse:
    headers = {"Retry-After": str(RETRY_AFTER_S)} if retry else None
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


async def upload(
    request: Request, backend: BackendClient, slot: Slot, cap: int, deadline_s: float
) -> JSONResponse:
    if declared_too_large(request.headers.get("content-length"), cap):
        return _answer(*_PROBLEMS["too_large"])
    if not slot.try_take():
        return _answer(429, "upload_busy", "another file is being uploaded", retry=True)
    reader = Reader(request, cap, deadline_s)
    try:
        try:
            relayed = await backend.upload(reader.chunks())
        except Exception:
            if reader.problem is None:
                raise
            return _answer(*_PROBLEMS[reader.problem])
        if reader.problem is not None:  # a transport that swallowed the stop still fails
            return _answer(*_PROBLEMS[reader.problem])
        return JSONResponse(relayed.body, status_code=relayed.status)
    finally:
        slot.release()
