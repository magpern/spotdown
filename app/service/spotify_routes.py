"""``POST /v2/spotify/resolve``: a Spotify track, album or playlist link to track metadata.

Behind the caller key like every ``/v2`` route. The link is parsed to (kind, id) and only the
fixed Web API URLs are fetched (``spotify_policy``). ``422 spotify_disabled`` without
``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET_FILE``; ``502 provider_error`` when Spotify
fails, with a fixed message.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from . import spotify_policy
from .deps import require_key
from .errors import ApiError
from .spotify_client import SpotifyClient, SpotifyFailure, load_client_secret

router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


def client_from_settings(settings: Settings) -> Optional[SpotifyClient]:
    """The client, or None when the credentials are not both set. A secret file that is set but
    unreadable or empty stops the start-up (fails closed, never a silent fallback)."""
    path = settings.spotify_client_secret_file
    if path is None or not settings.spotify_client_id:
        return None
    return SpotifyClient(settings.spotify_client_id, load_client_secret(path))


@router.post("/spotify/resolve")
def post_resolve(body: ResolveRequest, request: Request) -> JSONResponse:
    client: Optional[SpotifyClient] = request.app.state.spotify
    if client is None:
        raise ApiError(422, "spotify_disabled", "Spotify is not configured in this deployment")
    try:
        kind, sid = spotify_policy.parse_link(body.url)
    except spotify_policy.LinkError:
        raise ApiError(
            422, "invalid_request", "url must be a Spotify track, album or playlist link"
        ) from None
    try:
        tracks, truncated = client.resolve(kind, sid)
    except SpotifyFailure:
        raise ApiError(502, "provider_error", "the Spotify request failed") from None
    return JSONResponse({"tracks": tracks, "truncated": truncated})
