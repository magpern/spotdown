"""Lookups, ``GET /v2/analyses/{id}`` and ``DELETE /v2/scopes/{cache_scope}``.

Every one is limited to the caller's ``cache_scope``. A miss is always the same 404, so a lookup
never reveals that another scope holds the same bytes or source.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from . import analysis_doc, url_policy
from . import cache_policy as policy
from .api_models import ID_PATTERN, scope_problem
from .deps import require_key, settings_of, store_of
from .errors import ApiError
from .uploads import upload_path

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _checked_scope(value: Optional[str]) -> Optional[str]:
    """A scope that may be absent; when present it must be well formed."""
    if value is None or value == "":
        return None
    if scope_problem(value) is not None:
        raise ApiError(422, "invalid_request", "invalid cache_scope")
    return value


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "no such analysis in this scope")


def _lookup_ok(analysis_id: str) -> JSONResponse:
    return JSONResponse({"schema_version": "lookup-v1", "analysis_id": analysis_id})


def _normal_ref(kind: str, ref: str, allowed: frozenset) -> str:
    """The reference as provenance stores it. A ``url`` source is looked up by its digest
    (``sha256:<hex>``, the stored form, so a signed URL never travels in a query string); a
    raw URL is still normalised like a submitted one."""
    if kind == "url":
        if _URL_DIGEST.fullmatch(ref):
            return ref
        try:
            return url_policy.check_url(ref, allowed).ref
        except url_policy.UrlRefused:
            return ref  # neither a digest nor an allowed URL: never stored, so it misses
    if not _YOUTUBE_ID.fullmatch(ref):
        raise ApiError(422, "invalid_request", "invalid source")
    return ref


@router.get("/analyses/lookup")
def lookup(
    request: Request,
    source: Optional[str] = Query(default=None, max_length=2100),
    sha256: Optional[str] = Query(default=None, max_length=64),
    cache_scope: Optional[str] = Query(default=None, max_length=128),
) -> JSONResponse:
    store, settings = store_of(request), settings_of(request)
    if (source is None) == (sha256 is None):
        raise ApiError(422, "invalid_request", "give exactly one of source or sha256")
    scope = _checked_scope(cache_scope)
    version = analysis_doc.ANALYZER_VERSION_V2
    if sha256 is not None:
        if scope is None:
            raise ApiError(422, "scope_required", "a valid cache_scope is required")
        if not _SHA256.fullmatch(sha256):
            raise ApiError(422, "invalid_request", "sha256 must be 64 lower-case hex digits")
        hit = store.find_by_sha(scope, sha256, version)
    else:
        assert source is not None
        parsed = policy.parse_source(source)
        if parsed is None:
            raise ApiError(422, "invalid_request", "source must be <kind>:<ref>")
        kind, ref = parsed
        if scope is None and not policy.is_global_kind(kind):
            raise ApiError(422, "scope_required", "a valid cache_scope is required")
        allowed = url_policy.parse_allowlist(settings.url_allowed_hosts)
        hit = store.find_by_source(kind, _normal_ref(kind, ref, allowed), scope, version)
    if hit is None:
        raise _not_found()
    return _lookup_ok(hit.analysis_id)


@router.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    request: Request,
    cache_scope: Optional[str] = Query(default=None, max_length=128),
) -> JSONResponse:
    scope = _checked_scope(cache_scope)
    doc = None
    if re.fullmatch(ID_PATTERN, analysis_id):
        store = store_of(request)
        # A file-clock analysis, or an aligned one (its own id, composed on read).
        doc = store.get_analysis_in_scope(analysis_id, scope)
        if doc is None:
            doc = store.get_aligned_in_scope(analysis_id, scope)
    if doc is None:
        raise _not_found()
    return JSONResponse(doc)


@router.delete("/scopes/{cache_scope}", status_code=204)
def delete_scope(cache_scope: str, request: Request) -> Response:
    if scope_problem(cache_scope) is not None:
        raise ApiError(422, "invalid_request", "invalid cache_scope")
    settings = settings_of(request)
    for upload_id in store_of(request).delete_scope(cache_scope):
        upload_path(settings, upload_id).unlink(missing_ok=True)
    return Response(status_code=204)
