"""``PUT /v2/uploads``: stream audio bytes to a temporary file under a size cap.

The body is read chunk by chunk and refused once it passes ``MAX_UPLOAD_BYTES``; only mp3, mp4 and
wav are kept, recognised by their bytes and then checked whole (``container_io``: truncated,
malformed or mixed files and insane streams are refused). No file name is kept. A refused upload
leaves no file and no row. The file is deleted after the job that uses it, or once its TTL passes
unused.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

import anyio.to_thread
from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import Settings
from . import container_io, media_policy
from . import idempotency_policy as idem
from .api_models import scope_problem
from .container_policy import InvalidMedia
from .errors import ApiError
from .store import Store

ENDPOINT = "PUT /v2/uploads"


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_dir / upload_id


def purge_expired_uploads(settings: Settings, store: Store) -> None:
    """Deletes unused uploads past their TTL, and uploads whose job was lost (TTL plus the
    longest a job can run)."""
    grace = settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s
    for upload_id in store.stale_uploads(time.time(), grace):
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
    declared = request.headers.get("content-length")
    if declared is not None and (
        not declared.isdigit() or int(declared) > settings.max_upload_bytes
    ):
        raise ApiError(413, "too_large", "the upload is larger than the limit")

    purge_expired_uploads(settings, store)
    upload_id = "up_" + secrets.token_hex(16)
    final = upload_path(settings, upload_id)
    part = final.with_name(upload_id + ".part")
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with part.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise ApiError(413, "too_large", "the upload is larger than the limit")
                if len(head) < media_policy.SNIFF_BYTES:
                    head += chunk[: media_policy.SNIFF_BYTES - len(head)]
                digest.update(chunk)
                out.write(chunk)
        media = media_policy.sniff(head)
        if media is None:
            raise ApiError(415, "unsupported_media_type", "only mp3, mp4 and wav are accepted")
        try:  # the whole structure, off the event loop (it reads the file)
            await anyio.to_thread.run_sync(
                container_io.inspect_file, part, media, settings.analysis_max_decoded_s
            )
        except InvalidMedia:
            raise ApiError(
                415, "unsupported_media_type", "not a well-formed mp3, mp4 or wav file"
            ) from None
        req_hash = idem.request_hash(ENDPOINT, {"sha256": digest.hexdigest(), "bytes": size})
        recorded = store.idem_get(scope, idem_key)
        verdict = idem.decide(recorded, req_hash)
        if verdict == "conflict":
            raise ApiError(
                409, "idempotency_conflict", "this Idempotency-Key was used for another request"
            )
        if recorded is not None:  # the same upload again: the first response, nothing new kept
            part.unlink(missing_ok=True)
            return JSONResponse(recorded.body, status_code=recorded.status)
        answer = {"upload_id": upload_id, "sha256": digest.hexdigest(), "bytes": size}
        part.rename(final)
        store.add_upload(upload_id, scope, digest.hexdigest(), size, media, settings.upload_ttl_s)
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
