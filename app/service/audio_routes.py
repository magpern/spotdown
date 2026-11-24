"""``POST /v2/audio`` and ``GET /v2/audio/{audio_id}``: the mp3 of a matched YouTube video, behind
the key (ADR 0005 Decision 5). By the owner's direction (2026-11-24) this is the one ``/v2`` route
that returns audio; every other route still never does.

Only ``source_ref`` ``youtube:<video id>``, gated by ``ALLOW_YTDLP_SOURCING`` like every YouTube
path (``422 source_disabled`` before anything is queued); no URL is ever fetched. The
``Idempotency-Key`` follows the rules of ``POST /v2/analyses``. The job is polled with
``GET /v2/jobs/{id}`` (no ``cache_scope``) until it reports an ``audio_id``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import audio_policy
from . import idempotency_policy as idem
from .audio_files import audio_path
from .deps import require_key, settings_of, store_of
from .dispatch import Dispatch
from .errors import ApiError
from .routes import _youtube_ref, replay

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])

AUDIO = "POST /v2/audio"
IDEM_SCOPE = ""  # scope-less, like a youtube-only analysis request
_Artist = Annotated[str, Field(max_length=1000)]


class AudioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(min_length=1, max_length=256)
    bitrate: Literal[128, 192, 256, 320] = audio_policy.DEFAULT_BITRATE
    # Accepted long, then cleaned and capped for the ID3 tags and the file name.
    title: Optional[str] = Field(default=None, max_length=1000)
    artists: Optional[List[_Artist]] = Field(default=None, max_length=50)


@router.post("/audio", status_code=202)
def post_audio(
    body: AudioRequest, request: Request, idempotency_key: Optional[str] = Header(default=None)
) -> JSONResponse:
    settings, store = settings_of(request), store_of(request)
    video_id = _youtube_ref(body.source_ref, settings)  # 422 before anything is recorded
    problem = idem.key_problem(idempotency_key)
    if problem:
        raise ApiError(422, problem, "a valid Idempotency-Key header is required")
    assert idempotency_key is not None
    req_hash = idem.request_hash(AUDIO, body.model_dump(mode="json", exclude_none=True))
    replayed = replay(store, IDEM_SCOPE, idempotency_key, req_hash)
    if replayed is not None:
        return replayed

    job_id = "job_" + uuid.uuid4().hex
    store.add_audio_job(
        job_id,
        {
            "video_id": video_id,
            "bitrate": body.bitrate,
            "title": audio_policy.clean_text(body.title, audio_policy.TITLE_MAX),
            "artists": audio_policy.clean_artists(body.artists),
        },
        store.clock(),
    )
    answer = {"job_id": job_id, "status": "queued"}
    if not store.idem_put(IDEM_SCOPE, idempotency_key, req_hash, 202, answer):
        # A concurrent request with the same key recorded first: its response, or 409.
        store.delete_audio_job(job_id)
        winner = replay(store, IDEM_SCOPE, idempotency_key, req_hash)
        if winner is None:
            raise ApiError(
                409, "idempotency_conflict", "this Idempotency-Key was used for another request"
            )
        return winner
    dispatch: Dispatch = request.app.state.dispatch
    try:
        dispatch(job_id)
    except Exception:  # noqa: BLE001 - the queue is down; nothing was started
        store.delete_audio_job(job_id)
        store.idem_delete(IDEM_SCOPE, idempotency_key)
        raise ApiError(503, "queue_unavailable", "the job queue is unavailable") from None
    return JSONResponse(answer, status_code=202)


@router.get("/audio/{audio_id}")
def get_audio(audio_id: str, request: Request) -> FileResponse:
    """The kept mp3 as an attachment; ``404`` for a malformed, unknown or expired id."""
    if not audio_policy.is_audio_id(audio_id):
        raise ApiError(404, "not_found", "no such audio")
    settings, store = settings_of(request), store_of(request)
    kept = store.kept_audio(audio_id, store.clock())
    path = audio_path(settings, audio_id)
    if kept is None or not path.is_file():
        raise ApiError(404, "not_found", "no such audio")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{kept["file_name"]}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
