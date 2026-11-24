"""``POST /v2/spotify/resolve``: a Spotify track, album or playlist link to track metadata.

Behind the caller key like every ``/v2`` route. The link is parsed to (kind, id) and only the
public pages built from it on ``open.spotify.com`` are fetched (``spotify_policy``). No Spotify
setting or credential is read. ``502 provider_error`` when no page gives a usable track, with a
fixed message.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import spotify_policy
from .deps import require_key
from .errors import ApiError
from .spotify_client import SpotifyClient, SpotifyFailure

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


@router.post("/spotify/resolve")
def post_resolve(body: ResolveRequest, request: Request) -> JSONResponse:
    try:
        kind, sid = spotify_policy.parse_link(body.url)
    except spotify_policy.LinkError:
        raise ApiError(
            422, "invalid_request", "url must be a Spotify track, album or playlist link"
        ) from None
    client: SpotifyClient = request.app.state.spotify
    try:
        return JSONResponse(client.resolve(kind, sid))
    except SpotifyFailure:
        raise ApiError(502, "provider_error", "the Spotify request failed") from None
