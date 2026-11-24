"""Pure rules for what the browser may send and what goes to the backend (ADR 0005 Decision 7).

- Ids from the browser are checked by pattern before they go into a backend path.
- A backend body is built here from known fields only; the browser never chooses a host, a path
  prefix, a header or the ``cache_scope``.
- A backend answer is reduced to a JSON body and a status; a backend error to its ``code``.

No I/O.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, NamedTuple, Optional

# The scope of every analysis made from the UI (DELETE /v2/scopes/spotdown_ui removes them).
SCOPE = "spotdown_ui"
JSON_BODY_MAX = 64 * 1024  # a browser JSON body
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # the backend's own id pattern (job, analysis, upload)
YOUTUBE_REF = re.compile(r"youtube:[A-Za-z0-9_-]{11}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
BITRATES = (128, 192, 256, 320)
MAX_URL = 2048
MAX_TEXT = 512
MAX_ARTISTS = 20
MAX_TRACKS = 50
MAX_AUDIO_TEXT, MAX_AUDIO_ARTISTS = 1000, 50  # POST /v2/audio's own limits (docs/contract-v2.md)
MAX_DURATION_S = 7200.0


class Refused(ValueError):
    """A browser request the UI does not pass on: a status, a code and a fixed message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def _bad(message: str) -> Refused:
    return Refused(422, "invalid_request", message)


def checked_id(value: str) -> str:
    """An id that may go into a backend path, else ``Refused`` (404, as for an unknown id)."""
    if not ID.fullmatch(value):
        raise Refused(404, "not_found", "not found")
    return value


def error_body(code: str, message: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def parse_json(raw: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise _bad("the body must be a JSON object") from None
    if not isinstance(data, dict):
        raise _bad("the body must be a JSON object")
    return data


def _text(value: Any, name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise _bad(f"{name} must be a non-empty string of at most {limit} characters")
    return value


def _artists(value: Any, most: int = MAX_ARTISTS, limit: int = MAX_TEXT) -> List[str]:
    if not isinstance(value, list) or len(value) > most:
        raise _bad(f"artists must be a list of at most {most} strings")
    return [_text(a, "artists", limit) for a in value]


def _duration(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad(f"{name} must be a number")
    if not (math.isfinite(value) and 0 < value <= MAX_DURATION_S):
        raise _bad(f"{name} must be above 0 and at most {MAX_DURATION_S:g}")
    return float(value)


def _explicit(data: Dict[str, Any], out: Dict[str, Any]) -> None:
    if "explicit" in data and data["explicit"] is not None:
        if not isinstance(data["explicit"], bool):
            raise _bad("explicit must be true or false")
        out["explicit"] = data["explicit"]


def _youtube_ref(value: Any) -> str:
    if not isinstance(value, str) or not YOUTUBE_REF.fullmatch(value):
        raise _bad("source_ref must be youtube:<11-character video id>")
    return value


def resolve_body(data: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /v2/spotify/resolve``: the link only (the backend parses it strictly)."""
    return {"url": _text(data.get("url"), "url", MAX_URL)}


def _match_track(item: Any, index: int) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise _bad("each track must be an object")
    # Spotify's public pages may give no artists or duration (#76): an empty list and an absent
    # duration are passed on as they are, never invented.
    artists = item.get("artists")
    out: Dict[str, Any] = {
        "client_ref": str(index),
        "title": _text(item.get("title"), "title"),
        "artists": _artists([] if artists is None else artists),
    }
    if item.get("duration_s") is not None:
        out["duration_s"] = _duration(item["duration_s"], "duration_s")
    if item.get("isrc") is not None:
        out["isrc"] = _text(item["isrc"], "isrc", 32)
    _explicit(item, out)
    return out


def match_body(data: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /v2/match`` in the UI's scope, with the known track fields only."""
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= MAX_TRACKS:
        raise _bad(f"tracks must be a list of 1 to {MAX_TRACKS} tracks")
    return {"cache_scope": SCOPE, "tracks": [_match_track(t, i) for i, t in enumerate(tracks)]}


def _reference(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or value.get("kind") != "spotify":
        raise _bad("reference must be a spotify reference")
    ref_id = value.get("id")
    if not isinstance(ref_id, str) or not ID.fullmatch(ref_id):
        raise _bad("reference.id is not a valid id")
    duration = _duration(value.get("duration_s"), "duration_s")
    return {"kind": "spotify", "id": ref_id, "duration_s": duration}


def analysis_body(data: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /v2/analyses`` in the UI's scope: an ``upload_id`` or a ``youtube:`` ref."""
    out: Dict[str, Any] = {"cache_scope": SCOPE}
    has_upload, has_ref = data.get("upload_id") is not None, data.get("source_ref") is not None
    if has_upload == has_ref:
        raise _bad("send exactly one of upload_id or source_ref")
    if has_upload:
        upload_id = data["upload_id"]
        if not isinstance(upload_id, str) or not ID.fullmatch(upload_id):
            raise _bad("upload_id is not a valid id")
        out["upload_id"] = upload_id
    else:
        out["source_ref"] = _youtube_ref(data["source_ref"])
        if data.get("reference") is not None:
            out["reference"] = _reference(data["reference"])
    _explicit(data, out)
    return out


def audio_body(data: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /v2/audio``: a ``youtube:`` ref, a bitrate from the set and the optional tags."""
    bitrate = data.get("bitrate", 320)
    if isinstance(bitrate, bool) or bitrate not in BITRATES:
        raise _bad("bitrate must be one of 128, 192, 256 or 320")
    out: Dict[str, Any] = {"source_ref": _youtube_ref(data.get("source_ref")), "bitrate": bitrate}
    if data.get("title") is not None:
        out["title"] = _text(data["title"], "title", MAX_AUDIO_TEXT)
    if data.get("artists") is not None:
        out["artists"] = _artists(data["artists"], MAX_AUDIO_ARTISTS, MAX_AUDIO_TEXT)
    return out


class Relayed(NamedTuple):
    """What the browser gets for one backend answer: a status and a JSON body, nothing else."""

    status: int
    body: Dict[str, Any]


BACKEND_ERROR = Relayed(502, error_body("backend_error", "the backend did not answer usably"))
BACKEND_KEY = Relayed(502, error_body("backend_auth", "the backend refused the UI's key"))
BACKEND_TIMEOUT = Relayed(504, error_body("backend_timeout", "the backend did not answer in time"))
BACKEND_DOWN = Relayed(502, error_body("backend_unreachable", "the backend cannot be reached"))
# The backend's statuses that reach the browser as they are (with the backend's code only).
_PASSED_ON = frozenset({400, 404, 405, 408, 409, 413, 415, 422, 429})


def relay(status: int, content_type: Optional[str], raw: bytes) -> Relayed:
    """A backend answer as the browser sees it. A success is its JSON object; an error is its
    ``code`` with a fixed message (never the backend's message, headers or other fields)."""
    if status in (401, 403):
        return BACKEND_KEY
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        return BACKEND_ERROR
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return BACKEND_ERROR
    if not isinstance(data, dict):
        return BACKEND_ERROR
    if 200 <= status < 300:
        return Relayed(status, data)
    if status not in _PASSED_ON:
        return BACKEND_ERROR
    error = data.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        code = "backend_error"
    return Relayed(status, error_body(code, "the backend refused the request"))
