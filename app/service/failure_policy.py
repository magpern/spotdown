"""Maps why a job ended to its per-track outcome and failure kind (pure).

docs/contract-v2.md, "Per-track statuses": ``processing_failed`` carries a failure kind
(``decode_error``, ``timeout``, ``out_of_memory``, ``analysis_error``); the source statuses do not.
The resource caps (bytes, decoded length, memory) all end as ``out_of_memory``: they exist to bound
memory and are deterministic, so a caller does not retry them (it retries a ``timeout`` once).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple


class Reason(str, Enum):
    SOURCE_REFUSED = "source_refused"  # allowlist, private address, cross-host redirect
    SOURCE_MISSING = "source_missing"  # 403, 404, 410, 451, or an upload that has gone
    PROVIDER_ERROR = "provider_error"  # 5xx, a network failure, an encoded body
    RATE_LIMITED = "rate_limited"
    TOO_LARGE = "too_large"
    TOO_LONG = "too_long"
    MEMORY = "memory"
    DEADLINE = "deadline"
    CPU_LIMIT = "cpu_limit"
    DECODE = "decode"
    UNSUPPORTED_MEDIA = "unsupported_media"
    ANALYSIS = "analysis"


_TABLE = {
    Reason.SOURCE_REFUSED: ("source_unavailable", None),
    Reason.SOURCE_MISSING: ("source_unavailable", None),
    Reason.PROVIDER_ERROR: ("provider_error", None),
    Reason.RATE_LIMITED: ("rate_limited", None),
    Reason.TOO_LARGE: ("processing_failed", "out_of_memory"),
    Reason.TOO_LONG: ("processing_failed", "out_of_memory"),
    Reason.MEMORY: ("processing_failed", "out_of_memory"),
    Reason.DEADLINE: ("processing_failed", "timeout"),
    Reason.CPU_LIMIT: ("processing_failed", "timeout"),
    Reason.DECODE: ("processing_failed", "decode_error"),
    Reason.UNSUPPORTED_MEDIA: ("processing_failed", "decode_error"),
    Reason.ANALYSIS: ("processing_failed", "analysis_error"),
}


def outcome_for(reason: Reason) -> Tuple[str, Optional[str]]:
    """``(outcome, failure_kind)`` for a failed job."""
    return _TABLE[reason]


class JobFailure(Exception):
    """A job ended without an analysis, for ``reason``. Never carries a URL or a key."""

    def __init__(self, reason: Reason, retry_after_s: Optional[float] = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.retry_after_s = retry_after_s
