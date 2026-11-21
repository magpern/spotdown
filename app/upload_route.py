"""``POST /api/v1/analyze-upload``: analyse a local audio file from the standalone UI (ADR 0002).

Registered only when the standalone UI is served and ``fcntl`` (the slot) and ``resource`` (the
sandbox) exist; never in service mode. Every step, in order:

1. **Session.** ADR 0003's auth middleware answers ``401`` (``503`` for a token problem) before
   this route runs. As defence in depth the route also refuses (``401``) a request the middleware
   did not mark as authenticated in ``scope[SESSION_SCOPE_KEY]``, so the route never serves
   without a session, whatever the middleware stack.
2. **Request checks** (ADR 0002 Decision 2), before the slot and before any body byte:
   ``Content-Type`` exactly ``application/octet-stream`` (``415``), ``X-Spotdown-UI: 1``, one
   allowed ``Host`` and one same-origin ``Origin`` (``403``), a declared ``Content-Length`` over
   ``max_upload_bytes`` (``413``).
3. **The slot** (``upload_slot``): ``429`` with ``Retry-After`` if it is taken; the body is not
   read. Leftovers are swept while holding it.
4. **The body**, under the cap (``413``) and the read deadline ``standalone_upload_read_s``
   (``408``), then the shared hostile-input checks (``hostile_input``, ``415``), a cache hit on
   ``upl_<sha256>``, or the sandboxed analysis (``upload_job``).
5. **One ``finally``**, on a thread of its own and awaited under a shield: kill the child if it
   may still run and wait for it, delete the file and the work directory, and only then release
   the slot. A child that cannot be shown
   to have exited keeps the slot and its files until a restart.

No file name is kept, logged or returned, no tag is read into the output, and no audio is returned.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

import anyio
import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from . import origin_policy, upload_slot
from .config import Settings
from .service import hostile_input

PATH = "/api/v1/analyze-upload"
# Set to True in the ASGI scope by the standalone auth middleware (ADR 0003) for a request with a
# valid session. Anything else is refused here.
SESSION_SCOPE_KEY = "spotdown.session"
RETRY_AFTER_S = 10
UPLOAD_PREFIX = "upl_"

log = logging.getLogger("spotdown.upload_route")

# JobFailure reason -> (status, detail). Fixed texts: nothing from the file is ever echoed.
_FAILURES = {
    "unsupported_media": (415, hostile_input.MALFORMED[1]),
    "too_long": (422, "the audio is longer than the limit"),
    "decode": (422, "the file could not be decoded"),
    "memory": (422, "the analysis hit its memory limit"),
    "cpu_limit": (422, "the analysis hit its CPU time limit"),
    "deadline": (422, "the analysis hit its time limit"),
}


def available(settings: Settings) -> bool:
    if settings.service_mode or not settings.serve_ui or not upload_slot.AVAILABLE:
        return False
    try:
        import resource  # noqa: F401 - the sandbox's rlimits
    except ImportError:
        return False
    return True


def _answer(status: int, detail: str, headers: Optional[Dict[str, str]] = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def _one(request: Request, name: str) -> Optional[str]:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else None


def _refusal(request: Request, settings: Settings, hosts: FrozenSet[str]) -> Optional[JSONResponse]:
    """The first failed check before the slot, or None. Reads no body byte."""
    if request.scope.get(SESSION_SCOPE_KEY) is not True:
        return _answer(401, "sign in first")
    ctype = _one(request, "content-type")
    if ctype is None or ctype.strip().lower() != "application/octet-stream":
        return _answer(415, "send application/octet-stream")
    if _one(request, "x-spotdown-ui") != "1":
        return _answer(403, "not from the spotdown page")
    host = _one(request, "host")
    if not origin_policy.host_allowed(host, hosts):
        return _answer(403, "this host name is not allowed")
    if not origin_policy.origin_matches(_one(request, "origin"), settings.ui_public_origin, host):
        return _answer(403, "cross-origin request refused")
    if hostile_input.declared_too_large(
        request.headers.get("content-length"), settings.max_upload_bytes
    ):
        return _answer(413, hostile_input.TOO_LARGE[1])
    return None


class _Run:
    """One request's files and job, for the shielded ``finally``."""

    def __init__(self, directory: Path) -> None:
        name = "up_" + secrets.token_hex(16)
        self.audio = directory / name
        self.work = directory / (name + ".work")
        self.job: Any = None


async def _analyse(request: Request, settings: Settings, run: _Run, media: str, sha: str):
    from .analyze import ANALYZER_VERSION, V1_RESPONSE_VERSION, v1_response
    from .core.analyser import SR
    from .db import set_analysis
    from .service.failure_policy import JobFailure
    from .service.worker import limits_from
    from .upload_job import Job

    run.work.mkdir()
    run.job = job = Job(run.audio, media, run.work, limits_from(settings))
    outcome: Dict[str, Any] = {}
    async with anyio.create_task_group() as tg:

        async def disconnect() -> None:  # the body is read: the next message is the disconnect
            while (await request.receive())["type"] != "http.disconnect":
                pass
            tg.cancel_scope.cancel()

        tg.start_soon(disconnect)
        try:
            outcome["result"] = await job.run()
        except JobFailure as failure:
            outcome["failure"] = failure
        tg.cancel_scope.cancel()
    if "failure" in outcome:
        status, detail = _FAILURES.get(outcome["failure"].reason.value, (500, "analysis failed"))
        return _answer(status, detail)
    if "result" not in outcome:
        return _answer(400, "the client went away")
    result = outcome["result"]
    track_id = UPLOAD_PREFIX + sha
    body = v1_response(
        result["core"],
        track_id=track_id,
        sha256=sha,
        codec=str(result.get("probe", {}).get("codec") or media),
        sample_rate_hz=SR,
        spotify_duration_s=None,
    )
    text = json.dumps(body)
    await anyio.to_thread.run_sync(
        set_analysis, track_id, ANALYZER_VERSION, V1_RESPONSE_VERSION, text
    )
    return JSONResponse(body)


async def _receive(request: Request, settings: Settings, run: _Run):
    from .analyze import ANALYZER_VERSION, V1_RESPONSE_VERSION
    from .db import get_analysis

    try:
        with anyio.fail_after(settings.standalone_upload_read_s):
            got = await hostile_input.stream_to(
                request.stream(), run.audio, settings.max_upload_bytes
            )
        media = await hostile_input.check_file(run.audio, got.head, settings.analysis_max_decoded_s)
    except TimeoutError:
        return _answer(408, "the upload took too long")
    except ClientDisconnect:
        return _answer(400, "the client went away")
    except hostile_input.Refused as refused:
        return _answer(413 if refused.kind == "too_large" else 415, refused.detail)
    track_id = UPLOAD_PREFIX + got.sha256
    cached = await anyio.to_thread.run_sync(
        get_analysis, track_id, ANALYZER_VERSION, V1_RESPONSE_VERSION
    )
    if cached:  # the same bytes again
        return JSONResponse(json.loads(cached))
    return await _analyse(request, settings, run, media, got.sha256)


def _finish_blocking(slot: upload_slot.Slot, run: _Run) -> None:
    """Child first, then the files, then the slot."""
    if run.job is not None and not run.job.stop():
        log.error("local-file analysis child not shown to have exited; slot kept until restart")
        slot.keep_forever()
        return
    try:
        slot.sweep()  # this request's file and work directory (and nothing but the lock file)
    except OSError:
        log.error("local-file upload directory could not be emptied")
    slot.release()


async def _finish(slot: upload_slot.Slot, run: _Run) -> None:
    """Runs ``_finish_blocking`` on a thread of its own and waits for it, shielded.

    The steps run on that thread, not as awaits here: an anyio shield holds off anyio's own
    cancellation but not a repeated native ``Task.cancel()`` on asyncio, which would otherwise end
    this coroutine between the kill and the release. A cancellation that arrives anyway is held
    until the thread is done and then re-raised; the event loop stays free throughout."""
    worker = threading.Thread(
        target=_finish_blocking, args=(slot, run), name="upload-finish", daemon=True
    )
    worker.start()
    cancelled: Optional[BaseException] = None
    with anyio.CancelScope(shield=True):
        while worker.is_alive():
            try:
                await anyio.sleep(0.02)
            except anyio.get_cancelled_exc_class() as exc:
                cancelled = exc
    if cancelled is not None:
        raise cancelled


def register(app: FastAPI, settings: Settings) -> bool:
    """Adds the route if it is available (True). A bad host setting raises here."""
    if not available(settings):
        return False
    hosts = origin_policy.parse_allowed_hosts(settings.ui_allowed_hosts)
    if settings.ui_public_origin and not origin_policy.parse_origin(settings.ui_public_origin):
        raise ValueError("ui_public_origin must be scheme://host[:port]")
    directory = settings.standalone_uploads_dir
    directory.mkdir(parents=True, exist_ok=True)
    upload_slot.startup_sweep(directory)

    async def analyze_upload(request: Request):
        refused = _refusal(request, settings, hosts)
        if refused is not None:
            return refused
        slot = upload_slot.try_acquire(directory)
        if slot is None:
            return _answer(
                429, "another file is being analysed", {"Retry-After": str(RETRY_AFTER_S)}
            )
        run = _Run(directory)
        try:
            slot.sweep()
            return await _receive(request, settings, run)
        finally:
            await _finish(slot, run)

    def upload_available(request: Request):
        """The UI shows the file input only when this answers (the route is registered)."""
        if request.scope.get(SESSION_SCOPE_KEY) is not True:
            return _answer(401, "sign in first")
        return {"available": True}

    app.add_api_route(PATH, analyze_upload, methods=["POST"], include_in_schema=False)
    app.add_api_route(PATH, upload_available, methods=["GET"], include_in_schema=False)
    return True
