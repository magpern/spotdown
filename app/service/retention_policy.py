"""Retention of the analyses, their source links and the match records inside them: global
(public-source, e.g. YouTube; classbeat ADR 0040 §7) and scoped (spotdown#40). Pure: no I/O, the
clock is an argument.

No scope deletion removes a global entry, and a scope that is never deleted would keep its
entries for ever, so both are purged when not read for ``ANALYSIS_UNUSED_TTL_S`` (each by its own
setting, 90 days by default) or when their ``analyzer_version`` is no longer the current one (they
could never be a hit again). A read renews the entry; the renewal is written at
most once per ``TOUCH_INTERVAL_S``, so a hot entry is not a write on every read.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .cache_policy import is_global_kind, parse_source

ANALYSIS_UNUSED_TTL_S = 90 * 24 * 3600.0
TOUCH_INTERVAL_S = 3600.0


def unused_too_long(last_read_at: float, now: float, ttl_s: float) -> bool:
    """Not read for ``ttl_s`` or longer: purged."""
    return last_read_at <= now - ttl_s


def stale_version(entry_version: Optional[str], current: str) -> bool:
    """Of another analyzer version (or none recorded): never a hit again, so purged."""
    return entry_version != current


def purge_due(
    last_read_at: float, entry_version: Optional[str], *, now: float, ttl_s: float, current: str
) -> bool:
    return unused_too_long(last_read_at, now, ttl_s) or stale_version(entry_version, current)


def touch_due(last_read_at: float, now: float) -> bool:
    """A read renews the entry unless it was renewed within ``TOUCH_INTERVAL_S``."""
    return last_read_at <= now - TOUCH_INTERVAL_S


def parse_takedown_ref(value: str) -> Optional[Tuple[str, str]]:
    """``<kind>:<ref>`` of a global source (today ``youtube:<video id>``), or None. Only global
    sources are taken down by reference: any other source is scoped and goes with its scope."""
    parsed = parse_source(value)
    if parsed is None or not is_global_kind(parsed[0]):
        return None
    return parsed
