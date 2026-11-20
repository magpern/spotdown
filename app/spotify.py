from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

import httpx

from .config import get_settings
from .schemas import TrackMeta

_TOKEN: dict = {"value": None, "exp": 0.0}

# Matches open.spotify.com/track/<id>, /playlist/<id>, /album/<id> and spotify:track:<id>
_URL_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?|spotify:)"
    r"(?P<kind>track|playlist|album)[:/](?P<id>[A-Za-z0-9]+)"
)


class SpotifyError(RuntimeError):
    pass


def parse_spotify_url(url: str) -> Tuple[str, str]:
    m = _URL_RE.search(url or "")
    if not m:
        raise SpotifyError("No tracks found or invalid format")
    return m.group("kind"), m.group("id")


def _get_token() -> Optional[str]:
    settings = get_settings()
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        return None
    if _TOKEN["value"] and _TOKEN["exp"] > time.time() + 30:
        return _TOKEN["value"]
    resp = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(settings.spotify_client_id, settings.spotify_client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _TOKEN["value"] = data["access_token"]
    _TOKEN["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _TOKEN["value"]


def _track_from_api(item: dict) -> TrackMeta:
    album = item.get("album") or {}
    images = album.get("images") or []
    return TrackMeta(
        id=item["id"],
        title=item.get("name", ""),
        artists=[a["name"] for a in item.get("artists", [])],
        album=album.get("name"),
        duration_s=round(item.get("duration_ms", 0) / 1000) or None,
        isrc=(item.get("external_ids") or {}).get("isrc"),
        cover_url=images[0]["url"] if images else None,
        spotify_url=(item.get("external_urls") or {}).get("spotify"),
    )


def _oembed_fallback(url: str, track_id: str) -> TrackMeta:
    """No API creds: get title + thumbnail from the public oEmbed endpoint."""
    try:
        r = httpx.get("https://open.spotify.com/oembed", params={"url": url}, timeout=15)
        r.raise_for_status()
        d = r.json()
        return TrackMeta(
            id=track_id,
            title=d.get("title", track_id),
            cover_url=d.get("thumbnail_url"),
            spotify_url=url,
        )
    except Exception as exc:  # noqa: BLE001
        raise SpotifyError(f"oEmbed lookup failed: {exc}") from exc


def resolve(url: str) -> List[TrackMeta]:
    """Resolve a Spotify track/playlist/album URL to track metadata (no audio)."""
    kind, sid = parse_spotify_url(url)
    token = _get_token()

    if token is None:
        if kind != "track":
            raise SpotifyError("Set SPOTIFY_CLIENT_ID/SECRET to resolve playlists or albums")
        return [_oembed_fallback(url, sid)]

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers, timeout=20) as client:
        if kind == "track":
            r = client.get(f"https://api.spotify.com/v1/tracks/{sid}")
            r.raise_for_status()
            return [_track_from_api(r.json())]

        if kind == "album":
            r = client.get(f"https://api.spotify.com/v1/albums/{sid}")
            r.raise_for_status()
            album = r.json()
            out = []
            for it in album.get("tracks", {}).get("items", []):
                it.setdefault("album", {"name": album.get("name"), "images": album.get("images")})
                out.append(_track_from_api(it))
            return out

        # playlist
        out = []
        next_url = f"https://api.spotify.com/v1/playlists/{sid}/tracks?limit=100"
        while next_url:
            r = client.get(next_url)
            r.raise_for_status()
            page = r.json()
            for row in page.get("items", []):
                track = row.get("track")
                if track and track.get("id"):
                    out.append(_track_from_api(track))
            next_url = page.get("next")
        return out
