"""The v2 routes of this phase: ``PUT /v2/uploads``, ``POST /v2/analyses``, ``GET /v2/jobs/{id}``.

Sources: ``upload`` and ``url``. ``source_ref`` sources (``youtube``) are ``422 source_disabled``
until spotdown#10, and a ``reference`` (alignment) is refused until spotdown#11.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from . import url_policy
from .api_models import ID_PATTERN, AnalysisRequest, scope_problem
from .dispatch import Dispatch
from .errors import ApiError
from .service_key import ServiceKey
from .store import Store
from .uploads import purge_expired_uploads, receive_upload


def _require_key(request: Request) -> None:
    """Second line behind ServiceKeyMiddleware: every v2 route checks the key itself too."""
    key: ServiceKey = request.app.state.service_key
    if not key.matches_authorization(request.headers.get("authorization")):
        raise ApiError(401, "unauthorized", "unauthorized")


router = APIRouter(prefix="/v2", dependencies=[Depends(_require_key)])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _store(request: Request) -> Store:
    return request.app.state.store


@router.put("/uploads", status_code=201)
async def put_upload(request: Request) -> Dict[str, Any]:
    return await receive_upload(request, _settings(request), _store(request))


@router.post("/analyses", status_code=202)
def post_analysis(body: AnalysisRequest, request: Request) -> Dict[str, Any]:
    settings, store = _settings(request), _store(request)
    if body.source_count() != 1:
        raise ApiError(422, "invalid_request", "give exactly one of upload_id, source_ref or url")
    if body.source_ref is not None:
        raise ApiError(422, "source_disabled", "this source is disabled in this deployment")
    allowed = url_policy.parse_allowlist(settings.url_allowed_hosts)
    if body.url is not None and not allowed:
        raise ApiError(422, "source_disabled", "this source is disabled in this deployment")
    problem = scope_problem(body.cache_scope)
    if problem:
        raise ApiError(422, problem, "a valid cache_scope is required")
    if body.reference is not None:
        raise ApiError(422, "alignment_unavailable", "alignment is not available yet")

    job: Dict[str, Any] = {
        "job_id": "job_" + uuid.uuid4().hex,
        "cache_scope": body.cache_scope,
        "client_ref": body.client_ref,
    }
    if body.url is not None:
        try:
            checked = url_policy.check_url(body.url, allowed)
        except url_policy.UrlRefused as refused:
            raise ApiError(422, "url_not_allowed", f"url refused: {refused.reason}") from None
        job.update(source_kind="url", source_ref=checked.ref, fetch_url=body.url)
    else:
        purge_expired_uploads(settings, store)
        assert body.upload_id is not None and body.cache_scope is not None
        if store.claim_upload(body.upload_id, body.cache_scope) is None:
            raise ApiError(404, "upload_not_found", "no such upload in this scope")
        job.update(source_kind="upload", upload_id=body.upload_id)

    store.add_job(job)
    dispatch: Dispatch = request.app.state.dispatch
    try:
        dispatch(job["job_id"])
    except Exception:  # noqa: BLE001 - the queue is down; nothing was started
        store.delete_job(job["job_id"])
        if job.get("upload_id"):
            store.release_upload(job["upload_id"])
        raise ApiError(503, "queue_unavailable", "the job queue is unavailable") from None
    return {"job_id": job["job_id"], "status": "queued"}


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
        found = _store(request).get_job_in_scope(job_id, cache_scope)
    if found is None:
        raise ApiError(404, "not_found", "no such job in this scope")
    return JSONResponse(job_view(found))
