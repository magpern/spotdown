"""``PUT /v2/uploads``: stream audio bytes to a temporary file under a size cap.

The body is read chunk by chunk and refused once it passes ``MAX_UPLOAD_BYTES``; only mp3, mp4 and
wav are kept (recognised by their bytes). No file name is kept. The file is deleted after the job
that uses it, or once its TTL passes unused.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

from fastapi import Request

from ..config import Settings
from . import media_policy
from .api_models import scope_problem
from .errors import ApiError
from .store import Store


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_dir / upload_id


def purge_expired_uploads(settings: Settings, store: Store) -> None:
    """Deletes unused uploads past their TTL, and uploads whose job was lost (TTL plus the
    longest a job can run)."""
    grace = settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s
    for upload_id in store.stale_uploads(time.time(), grace):
        upload_path(settings, upload_id).unlink(missing_ok=True)
        store.delete_upload(upload_id)


async def receive_upload(request: Request, settings: Settings, store: Store) -> dict:
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
        part.rename(final)
        store.add_upload(upload_id, scope, digest.hexdigest(), size, media, settings.upload_ttl_s)
    except BaseException:
        part.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    return {"upload_id": upload_id, "sha256": digest.hexdigest(), "bytes": size}
