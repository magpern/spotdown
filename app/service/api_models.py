"""Request shapes and field rules of the v2 API (docs/contract-v2.md). No I/O."""

from __future__ import annotations

import re
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

SCOPE_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
_SCOPE = re.compile(SCOPE_PATTERN)
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def scope_problem(value: Optional[str]) -> Optional[str]:
    """The error code for a ``cache_scope`` that a source requires, or None when it is valid."""
    if value is None or value == "":
        return "scope_required"
    if not _SCOPE.fullmatch(value):
        return "invalid_request"
    return None


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=256)
    duration_s: float = Field(gt=0, le=7200)


class AnalysisRequest(BaseModel):
    """``POST /v2/analyses``: exactly one of ``upload_id``, ``source_ref`` or ``url``."""

    model_config = ConfigDict(extra="forbid")

    cache_scope: Optional[str] = Field(default=None, max_length=128)
    client_ref: Optional[str] = Field(default=None, min_length=1, max_length=128)
    upload_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    source_ref: Optional[str] = Field(default=None, min_length=1, max_length=256)
    url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    reference: Optional[Reference] = None
    # The caller's explicit-lyrics flag: reported as evidence in ``content.explicit``, never a gate.
    explicit: Optional[StrictBool] = None

    def source_count(self) -> int:
        return sum(v is not None for v in (self.upload_id, self.source_ref, self.url))


MAX_MATCH_TRACKS = 50
ArtistName = Annotated[str, Field(min_length=1, max_length=200)]


class MatchTrack(BaseModel):
    """One track of ``POST /v2/match``: metadata only, no Spotify call is made."""

    model_config = ConfigDict(extra="forbid")

    client_ref: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    # artists and duration_s may be left out when the caller has no such metadata (a Spotify
    # public page that did not state it, spotdown#76): never scored as a mismatch. An explicit
    # empty list is still refused.
    artists: Optional[List[ArtistName]] = Field(default=None, min_length=1, max_length=10)
    duration_s: Optional[float] = Field(default=None, gt=0, le=7200)
    isrc: Optional[str] = Field(default=None, min_length=1, max_length=32)
    # Kept with the match record of the chosen video; evidence for its analysis, never a gate.
    explicit: Optional[StrictBool] = None


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_scope: Optional[str] = Field(default=None, max_length=128)
    tracks: List[MatchTrack] = Field(min_length=1, max_length=MAX_MATCH_TRACKS)
