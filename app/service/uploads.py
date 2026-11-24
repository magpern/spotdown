"""``PUT /v2/uploads``: stream audio bytes to a temporary file under a size cap.

The body is read chunk by chunk and refused once it passes ``MAX_UPLOAD_BYTES``; only mp3, mp4 and
wav are kept, recognised by their bytes and then checked whole (``hostile_input``, shared with
the standalone local-file route: truncated, malformed or mixed files and insane streams are
refused). No file name is kept. A refused upload
leaves no file and no row. The file is deleted after the job that uses it, or once its TTL passes
unused.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import Settings
from . import hostile_input
from . import idempotency_policy as idem
from .api_models import scope_problem
from .errors import ApiError
from .store import Store

ENDPOINT = "PUT /v2/uploads"


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_dir / upload_id


def purge_expired_uploads(settings: Settings, store: Store, now: Optional[float] = None) -> None:
    """Deletes unused uploads past their TTL, and uploads whose job was lost (TTL plus the
    longest a job can run), never one a running job uses. Run on upload requests and by the
    worker's sweep, so no upload outlives its TTL because no new upload arrives."""
    grace = settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s
    for upload_id in store.stale_uploads(now or time.time(), grace):
        upload_path(settings, upload_id).unlink(missing_ok=True)
        store.delete_upload(upload_id)


async def receive_upload(
    request: Request, settings: Settings, store: Store, idem_key: str
) -> JSONResponse:
    scope = request.query_params.get("cache_scope")
    problem = scope_problem(scope)
    if problem:
        raise ApiError(422, problem, "a valid cache_scope is required")
    assert scope is not None
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/octet-stream":
        raise ApiError(415, "unsupported_media_type", "send application/octet-stream")
    if hostile_input.declared_too_large(
        request.headers.get("content-length"), settings.max_upload_bytes
    ):
        raise ApiError(413, *hostile_input.TOO_LARGE)

    purge_expired_uploads(settings, store)
    upload_id = "up_" + secrets.token_hex(16)
    final = upload_path(settings, upload_id)
    part = final.with_name(upload_id + ".part")
    try:
        try:  # the shared hostile-input checks (ADR 0002, Decision 4)
            got = await hostile_input.stream_to(request.stream(), part, settings.max_upload_bytes)
            media = await hostile_input.check_file(part, got.head, settings.analysis_max_decoded_s)
        except hostile_input.Refused as refused:
            status = 413 if refused.kind == "too_large" else 415
            raise ApiError(status, refused.kind, refused.detail) from None
        digest, size = got.sha256, got.size
        req_hash = idem.request_hash(ENDPOINT, {"sha256": digest, "bytes": size})
        recorded = store.idem_get(scope, idem_key)
        verdict = idem.decide(recorded, req_hash)
        if verdict == "conflict":
            raise ApiError(
                409, "idempotency_conflict", "this Idempotency-Key was used for another request"
            )
        if recorded is not None:  # the same upload again: the first response, nothing new kept
            part.unlink(missing_ok=True)
            return JSONResponse(recorded.body, status_code=recorded.status)
        answer = {"upload_id": upload_id, "sha256": digest, "bytes": size}
        part.rename(final)
        store.add_upload(upload_id, scope, digest, size, media, settings.upload_ttl_s)
        if not store.idem_put(scope, idem_key, req_hash, 201, answer):
            # A concurrent upload with the same key won: its response for the same bytes (this
            # one's file and row are dropped), 409 for other bytes.
            winner = store.idem_get(scope, idem_key)
            if winner is None or idem.decide(winner, req_hash) == "conflict":
                raise ApiError(409, "idempotency_conflict", "this Idempotency-Key is in use")
            final.unlink(missing_ok=True)
            store.delete_upload(upload_id)
            return JSONResponse(winner.body, status_code=winner.status)
    except BaseException:
        part.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        store.delete_upload(upload_id)
        raise
    return JSONResponse(answer, status_code=201)
