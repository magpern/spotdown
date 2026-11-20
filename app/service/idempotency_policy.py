"""``Idempotency-Key`` rules (docs/contract-v2.md, Common rules). Pure: no I/O."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

WINDOW_S = 24 * 3600
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class Recorded:
    request_hash: str
    status: int
    body: Any


def key_problem(value: Optional[str]) -> Optional[str]:
    """The error code for a missing or malformed key, or None."""
    if value is None or value == "":
        return "idempotency_key_required"
    return None if _KEY.fullmatch(value) else "invalid_request"


def request_hash(endpoint: str, payload: Any) -> str:
    """A stable hash of what the request asked for (endpoint plus canonical body)."""
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{endpoint}\n{canon}".encode()).hexdigest()


def decide(recorded: Optional[Recorded], current_hash: str) -> str:
    """``new`` (no live record), ``replay`` (same request) or ``conflict`` (key reused)."""
    if recorded is None:
        return "new"
    return "replay" if recorded.request_hash == current_hash else "conflict"


def expired(created_at: float, now: float) -> bool:
    return now - created_at >= WINDOW_S
