"""The browser-facing routes: the page and its assets, and one ``/api`` route per fixed backend
route (ADR 0005 Decision 7). Each handler builds the backend request from known fields only
(``api_policy``) and relays only a status and a JSON body, never the backend's headers.

| Browser                     | Backend                                  |
|-----------------------------|------------------------------------------|
| ``POST /api/spotify/resolve`` | ``POST /v2/spotify/resolve``           |
| ``POST /api/match``         | ``POST /v2/match`` (scope ``spotdown_ui``) |
| ``PUT /api/uploads``        | ``PUT /v2/uploads`` (streamed)           |
| ``POST /api/analyses``      | ``POST /v2/analyses`` (scope, fresh ``Idempotency-Key``) |
| ``GET /api/jobs/{id}``      | ``GET /v2/jobs/{id}``                    |
| ``GET /api/analyses/{id}``  | ``GET /v2/analyses/{id}``                |
| ``POST /api/audio``         | ``POST /v2/audio`` (fresh ``Idempotency-Key``) |
| ``GET /api/audio/{id}``     | ``GET /v2/audio/{id}``, streamed as ``audio/mpeg`` |
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import api_policy as policy
from . import session_policy as paths
from . import upload_proxy
from .backend import BackendClient
from .settings import UiSettings

STATIC_DIR = Path(__file__).parent / "static"
ASSETS = {
    "ui.css": "text/css",
    "ui_common.js": "text/javascript",
    "analysis_model.js": "text/javascript",
    "analysis_timeline.js": "text/javascript",
    "analysis_view.js": "text/javascript",
    "waveform_model.js": "text/javascript",
    "waveform_view.js": "text/javascript",
    "waveform_player.js": "text/javascript",
    "ui_api.js": "text/javascript",
    "ui_link.js": "text/javascript",
    "ui_upload.js": "text/javascript",
}


def _json(relayed: policy.Relayed) -> JSONResponse:
    return JSONResponse(relayed.body, status_code=relayed.status)


def _refused(exc: policy.Refused) -> JSONResponse:
    return JSONResponse(policy.error_body(exc.code, exc.message), status_code=exc.status)


async def _body(request: Request) -> Dict[str, Any]:
    """The browser's JSON object, at most ``JSON_BODY_MAX`` bytes (read no further)."""
    if upload_proxy.declared_too_large(request.headers.get("content-length"), policy.JSON_BODY_MAX):
        raise policy.Refused(413, "too_large", "the request body is too large")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > policy.JSON_BODY_MAX:
            raise policy.Refused(413, "too_large", "the request body is too large")
    return policy.parse_json(bytes(raw))


def _create(
    backend: BackendClient, name: str, build: Callable[[Dict[str, Any]], Dict[str, Any]],
    idempotent: bool,
):  # fmt: skip
    async def handler(request: Request) -> JSONResponse:
        try:
            body = build(await _body(request))
        except policy.Refused as exc:
            return _refused(exc)
        return _json(await backend.call(name, json=body, idempotent=idempotent))

    return handler


def _read(backend: BackendClient, name: str):
    async def handler(ident: str) -> JSONResponse:
        try:
            checked = policy.checked_id(ident)
        except policy.Refused as exc:
            return _refused(exc)
        return _json(await backend.call(name, checked, scoped=True))

    return handler


async def _capped(response: httpx.Response, cap: int) -> AsyncIterator[bytes]:
    """The mp3's bytes; past the cap the stream ends early (the browser sees a broken download,
    never more than the cap)."""
    sent = 0
    async for chunk in response.aiter_bytes():
        sent += len(chunk)
        if sent > cap:
            return
        yield chunk


def _audio(backend: BackendClient, cap: int):
    async def handler(ident: str) -> Response:
        try:
            checked = policy.checked_id(ident)
        except policy.Refused as exc:
            return _refused(exc)
        response, relayed = await backend.open_audio(checked)
        if response is None:
            return _json(relayed)
        headers = {"Content-Disposition": f'attachment; filename="spotdown-{checked}.mp3"'}
        return StreamingResponse(
            _capped(response, cap),
            media_type="audio/mpeg",
            headers=headers,
            background=BackgroundTask(response.aclose),
        )

    return handler


def _page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


def _asset(name: str) -> Response:
    """A fixed list, so no path is ever built from input."""
    if name not in ASSETS:
        return JSONResponse(policy.error_body("not_found", "not found"), status_code=404)
    return FileResponse(STATIC_DIR / name, media_type=ASSETS[name])


def _healthz() -> Dict[str, bool]:
    """Open, and never calls the backend."""
    return {"ok": True}


def register(app: FastAPI, settings: UiSettings, backend: BackendClient) -> None:
    slot = upload_proxy.Slot()

    async def put_upload(request: Request) -> JSONResponse:
        return await upload_proxy.upload(
            request, backend, slot, settings.max_upload_bytes, settings.upload_read_s
        )

    add = app.add_api_route
    add("/healthz", _healthz, methods=["GET"])
    add(paths.RESOLVE_PATH, _create(backend, "resolve", policy.resolve_body, False),
        methods=["POST"])  # fmt: skip
    add(paths.MATCH_PATH, _create(backend, "match", policy.match_body, False), methods=["POST"])
    add(paths.UPLOAD_PATH, put_upload, methods=["PUT"])
    add(paths.ANALYSES_PATH, _create(backend, "analyse", policy.analysis_body, True),
        methods=["POST"])  # fmt: skip
    add("/api/jobs/{ident}", _read(backend, "job"), methods=["GET"])
    add("/api/analyses/{ident}", _read(backend, "analysis"), methods=["GET"])
    add(paths.AUDIO_PATH, _create(backend, "audio_job", policy.audio_body, True), methods=["POST"])
    add("/api/audio/{ident}", _audio(backend, settings.max_audio_bytes), methods=["GET"])
    add("/", _page, methods=["GET"])
    # Matches every single-segment path, so it comes last.
    add("/{name}", _asset, methods=["GET"])
