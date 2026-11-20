from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TrackMeta(BaseModel):
    """Normalized track metadata. `id` is the cache key (track_id)."""

    id: str                      # e.g. spotify track id, or a hash for source_url jobs
    title: str
    artists: List[str] = []
    album: Optional[str] = None
    duration_s: Optional[int] = None
    isrc: Optional[str] = None
    cover_url: Optional[str] = None
    spotify_url: Optional[str] = None

    @property
    def artist(self) -> str:
        return ", ".join(self.artists) if self.artists else ""

    @property
    def search_query(self) -> str:
        return f"{self.artist} {self.title}".strip()


class ConvertRequest(BaseModel):
    # Provide exactly one of these:
    spotify_url: Optional[str] = None       # resolves metadata, then sources audio (gated)
    source_url: Optional[str] = None        # a direct URL you have the right to download

    bitrate: Optional[int] = Field(default=None, ge=64, le=320)
    format: Optional[str] = Field(default=None, pattern="^(mp3|m4a)$")


class ConvertResponse(BaseModel):
    status: str                              # "done" | "queued"
    task_id: Optional[str] = None
    download_url: Optional[str] = None
    track: Optional[TrackMeta] = None


class TaskStatus(BaseModel):
    status: str                              # queued | processing | done | failed
    download_url: Optional[str] = None
    error: Optional[str] = None
