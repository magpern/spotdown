"""The v2 routes of this phase: ``PUT /v2/uploads``, ``POST /v2/analyses``, ``GET /v2/jobs/{id}``.

Sources: ``upload``, ``url`` and ``youtube`` (``source_ref`` ``youtube:<video id>``, ``422
source_disabled`` unless ``ALLOW_YTDLP_SOURCING=true``; its ``cache_scope`` is optional and its
analysis is global). A ``reference`` (alignment) is refused until spotdown#11. Lookups,
``GET /v2/analyses/{id}`` and the scope deletion are in ``cache_routes``; the matcher in
``match_routes``.

``POST /v2/analyses`` answers from the cache when it can (equal sha256 and ``analyzer_version`` in
the caller's scope, or the same source reference in that scope; a global ``youtube`` link in
any scope): ``200`` with a job that is already ``done``, no queue, no analysis. Both it and
``PUT /v2/uploads`` need an ``Idempotency-Key``.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from ..youtube.normalise_policy import VIDEO_ID
from . import analysis_doc, url_policy
from . import idempotency_policy as idem
from .api_models import ID_PATTERN, AnalysisRequest, scope_problem
from .deps import require_key, settings_of, store_of
from .dispatch import Dispatch
from .errors import ApiError
from .uploads import purge_expired_uploads, receive_upload, upload_path

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])

ANALYSES = "POST /v2/analyses"


@router.put("/uploads", status_code=201)
async def put_upload(
    request: Request, idempotency_key: Optional[str] = Header(default=None)
) -> JSONResponse:
    problem = idem.key_problem(idempotency_key)
    if problem:
        raise ApiError(422, problem, "a valid Idempotency-Key header is required")
    assert idempotency_key is not None
    return await receive_upload(request, settings_of(request), store_of(request), idempotency_key)


def replay(store: Any, scope: str, key: str, req_hash: str) -> Optional[JSONResponse]:
    """The first response for this key, ``409`` when the key was used for another request."""
    recorded = store.idem_get(scope, key)
    verdict = idem.decide(recorded, req_hash)
    if verdict == "conflict":
        raise ApiError(
            409, "idempotency_conflict", "this Idempotency-Key was used for another request"
        )
    if recorded is not None:
        return JSONResponse(recorded.body, status_code=recorded.status)
    return None


def _youtube_ref(source_ref: str, settings: Any) -> str:
    """The 11-character video id of ``youtube:<id>``; the source is off unless enabled."""
    kind, sep, ref = source_ref.partition(":")
    if not sep or kind != "youtube":
        raise ApiError(422, "invalid_request", "source_ref must be youtube:<video id>")
    if not settings.allow_ytdlp_sourcing:
        raise ApiError(422, "source_disabled", "this source is disabled in this deployment")
    if not VIDEO_ID.match(ref):
        raise ApiError(422, "invalid_request", "a youtube reference is an 11-character video id")
    return ref


@router.post("/analyses")
def post_analysis(
    body: AnalysisRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    settings, store = settings_of(request), store_of(request)
    if body.source_count() != 1:
        raise ApiError(422, "invalid_request", "give exactly one of upload_id, source_ref or url")
    video_id = _youtube_ref(body.source_ref, settings) if body.source_ref is not None else None
    allowed = url_policy.parse_allowlist(settings.url_allowed_hosts)
    if body.url is not None and not allowed:
        raise ApiError(422, "source_disabled", "this source is disabled in this deployment")
    problem = scope_problem(body.cache_scope)
    if problem and not (video_id is not None and body.cache_scope is None):
        # Optional for a global (youtube) source; required and valid for every other one.
        raise ApiError(422, problem, "a valid cache_scope is required")
    if body.reference is not None:
        raise ApiError(422, "alignment_unavailable", "alignment is not available yet")
    problem = idem.key_problem(idempotency_key)
    if problem:
        raise ApiError(422, problem, "a valid Idempotency-Key header is required")
    assert idempotency_key is not None
    scope, key = body.cache_scope, idempotency_key
    idem_scope = scope or ""  # a scope-less (youtube-only) request keys under the global ""
    req_hash = idem.request_hash(ANALYSES, body.model_dump(mode="json", exclude_none=True))
    replayed = replay(store, idem_scope, key, req_hash)
    if replayed is not None:
        return replayed

    job: Dict[str, Any] = {
        "job_id": "job_" + uuid.uuid4().hex,
        "cache_scope": scope,
        "client_ref": body.client_ref,
    }
    version = analysis_doc.ANALYZER_VERSION_V2
    hit = None
    if video_id is not None:
        job.update(source_kind="youtube", source_ref=video_id)
        hit = store.find_by_source("youtube", video_id, scope, version)
    elif body.url is not None:
        try:
            checked = url_policy.check_url(body.url, allowed)
        except url_policy.UrlRefused as refused:
            raise ApiError(422, "url_not_allowed", f"url refused: {refused.reason}") from None
        job.update(source_kind="url", source_ref=checked.ref, fetch_url=body.url)
        hit = store.find_by_source("url", checked.ref, scope, version)
    else:
        purge_expired_uploads(settings, store)
        assert body.upload_id is not None and scope is not None
        claimed = store.claim_upload(body.upload_id, scope)
        if claimed is None:
            raise ApiError(404, "upload_not_found", "no such upload in this scope")
        job.update(source_kind="upload", upload_id=body.upload_id)
        hit = store.find_by_sha(scope, claimed["sha256"], version)

    if hit is not None:  # a cache hit: no analysis, and the audio is not needed any more
        job.pop("fetch_url", None)
        used_upload = job.pop("upload_id", None)
        store.add_job_done(job, hit.analysis_id)
        if used_upload:
            upload_path(settings, used_upload).unlink(missing_ok=True)
            store.delete_upload(used_upload)
        answer: Dict[str, Any] = {
            "job_id": job["job_id"],
            "status": "done",
            "analysis_id": hit.analysis_id,
        }
        return _record(store, idem_scope, key, req_hash, 200, answer, job["job_id"])[0]

    store.add_job(job)
    answer = {"job_id": job["job_id"], "status": "queued"}
    response, won = _record(store, idem_scope, key, req_hash, 202, answer, job["job_id"])
    if not won:
        return response
    dispatch: Dispatch = request.app.state.dispatch
    try:
        dispatch(job["job_id"])
    except Exception:  # noqa: BLE001 - the queue is down; nothing was started
        store.delete_job(job["job_id"])
        store.idem_delete(idem_scope, key)
        if job.get("upload_id"):
            store.release_upload(job["upload_id"])
        raise ApiError(503, "queue_unavailable", "the job queue is unavailable") from None
    return response


def _record(
    store: Any,
    scope: str,
    key: str,
    req_hash: str,
    status: int,
    answer: Dict[str, Any],
    job_id: str,
) -> Tuple[JSONResponse, bool]:
    """Remembers the first response for the key. If a concurrent request with the same key won,
    this job is dropped and the winner's response is returned instead."""
    if store.idem_put(scope, key, req_hash, status, answer):
        return JSONResponse(answer, status_code=status), True
    store.delete_job(job_id)
    winner = store.idem_get(scope, key)
    assert winner is not None
    return JSONResponse(winner.body, status_code=winner.status), False


def job_view(job: Dict[str, Any]) -> Dict[str, Any]:
    """A stored job as job-v1 (schema/job-v1.json)."""
    view: Dict[str, Any] = {
        "schema_version": "job-v1",
        "job_id": job["job_id"],
        "status": job["status"],
        "outcome": job["outcome"] if job["status"] == "failed" else None,
    }
    if job["client_ref"] is not None:
        view["client_ref"] = job["client_ref"]
    if job["status"] == "done":
        view["analysis_id"] = job["analysis_id"]
        view["failure_kind"] = None
    elif job["status"] == "failed":
        view["failure_kind"] = job["failure_kind"]
        if job["outcome"] == "rate_limited":
            view["retry_after_s"] = job["retry_after_s"] or 0.0
    return view


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    request: Request,
    cache_scope: Optional[str] = Query(default=None, max_length=128),
) -> JSONResponse:
    found = None
    if re.match(ID_PATTERN, job_id):
        # Jobs are visible only in the scope that created them; a miss reveals nothing.
        found = store_of(request).get_job_in_scope(job_id, cache_scope)
    if found is None:
        raise ApiError(404, "not_found", "no such job in this scope")
    return JSONResponse(job_view(found))
