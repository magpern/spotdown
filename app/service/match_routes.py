"""``POST /v2/match``: metadata to ranked YouTube candidates. No download, no analysis.

The matcher is the ``youtube`` source's, so it is ``422 source_disabled`` when the operator set
``ALLOW_YTDLP_SOURCING=false`` (on by default). ``cache_scope`` is optional (global sources only);
when given it must be valid and only decides which cached analyses a candidate may report.
``Idempotency-Key`` is optional; when given it behaves as on ``POST /v2/analyses``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from . import idempotency_policy as idem
from .api_models import MatchRequest, scope_problem
from .deps import require_key, settings_of, store_of
from .errors import ApiError
from .match_service import Matcher
from .routes import replay

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])

MATCH = "POST /v2/match"


@router.post("/match")
def post_match(
    body: MatchRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    settings, store = settings_of(request), store_of(request)
    if not settings.allow_ytdlp_sourcing:
        raise ApiError(422, "source_disabled", "this source is disabled in this deployment")
    if body.cache_scope is not None and scope_problem(body.cache_scope) is not None:
        raise ApiError(422, "invalid_request", "invalid cache_scope")
    idem_scope = body.cache_scope or ""
    req_hash = None
    if idempotency_key is not None:
        problem = idem.key_problem(idempotency_key)
        if problem:
            raise ApiError(422, problem, "a valid Idempotency-Key header is required")
        req_hash = idem.request_hash(MATCH, body.model_dump(mode="json", exclude_none=True))
        replayed = replay(store, idem_scope, idempotency_key, req_hash)
        if replayed is not None:
            return replayed
    matcher = Matcher(
        store,
        request.app.state.youtube,
        search_results=settings.source_search_results,
        concurrency=settings.match_concurrency,
    )
    answer = {"results": matcher.match_all(body.tracks, body.cache_scope)}
    if idempotency_key is not None and req_hash is not None:
        if not store.idem_put(idem_scope, idempotency_key, req_hash, 200, answer):
            replayed = replay(store, idem_scope, idempotency_key, req_hash)
            if replayed is not None:
                return replayed
    return JSONResponse(answer)
