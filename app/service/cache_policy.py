"""Cache keys and the hit rule (docs/contract-v2.md, plan 4a). Pure: no I/O.

* Audio-analysis cache: key = file sha256 + ``analyzer_version``, inside one scope.
* Source link: key = (source kind, source ref, scope); the scope is global for ``youtube`` and the
  caller's ``cache_scope`` for every other source.
* The decoded-audio fingerprint is recorded on an entry but is never part of a key and never
  decides a hit: this module has no code path that reads it.
* Alignment cache: key = audio identity (sha256), reference and ``alignment_version``, per scope.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

GLOBAL_SOURCE_KINDS = frozenset({"youtube"})
LOOKUP_SOURCE_KINDS = frozenset({"youtube", "url"})


@dataclass(frozen=True)
class CacheEntry:
    """One stored analysis as the cache sees it. ``scope`` is None for a global entry."""

    analysis_id: str
    scope: Optional[str]
    sha256: str
    analyzer_version: str
    fingerprint: Optional[str] = None


@dataclass(frozen=True)
class IndexFields:
    sha256: str
    fingerprint: Optional[str]
    analyzer_version: str
    source_kind: str
    source_ref: Optional[str]


def is_global_kind(kind: str) -> bool:
    return kind in GLOBAL_SOURCE_KINDS


def link_scope(kind: str, cache_scope: Optional[str]) -> Optional[str]:
    """The scope a source reference is keyed in: None (global) for ``youtube``."""
    return None if is_global_kind(kind) else cache_scope


def scope_key(scope: Optional[str]) -> str:
    """The stored form of a scope: ``""`` is global (valid scopes are never empty)."""
    return scope or ""


def parse_source(value: str) -> Optional[Tuple[str, str]]:
    """``<kind>:<ref>`` as ``(kind, ref)``, or None when it is not a lookup-able source."""
    kind, sep, ref = value.partition(":")
    if not sep or kind not in LOOKUP_SOURCE_KINDS or not ref or len(ref) > 2048:
        return None
    return kind, ref


def usable(entry: CacheEntry, scope: Optional[str], analyzer_version: str) -> bool:
    """An entry may be reused only in its own scope and at the current analyzer version."""
    return entry.scope == scope and entry.analyzer_version == analyzer_version


def content_hit(
    entries: Iterable[CacheEntry], *, scope: str, sha256: str, analyzer_version: str
) -> Optional[CacheEntry]:
    """The entry for these exact bytes in ``scope``. Equal sha256 is the only content match: an
    equal fingerprint with another sha256 is a miss."""
    for entry in entries:
        if entry.sha256 == sha256 and usable(entry, scope, analyzer_version):
            return entry
    return None


def source_hit(
    entry: Optional[CacheEntry], *, kind: str, cache_scope: Optional[str], analyzer_version: str
) -> Optional[CacheEntry]:
    """The linked entry when it lives in the source's scope at the current analyzer version."""
    if entry is None:
        return None
    return entry if usable(entry, link_scope(kind, cache_scope), analyzer_version) else None


def readable_in(entry_scope: Optional[str], caller_scope: Optional[str]) -> bool:
    """A caller reads its own scope's analyses and global ones, nothing else."""
    return entry_scope is None or entry_scope == caller_scope


def index_fields(doc: Dict[str, Any]) -> IndexFields:
    prov = doc["provenance"]
    source = prov["source"]
    return IndexFields(
        sha256=prov["audio"]["sha256"],
        fingerprint=prov["audio"].get("fingerprint"),
        analyzer_version=prov["analyzer"]["analyzer_version"],
        source_kind=source["kind"],
        source_ref=source.get("ref"),
    )


def alignment_key(
    *,
    sha256: str,
    reference_kind: str,
    reference_id: str,
    reference_duration_s: float,
    alignment_version: str,
) -> str:
    """Audio identity + reference + ``alignment_version``; any change makes a new key."""
    payload = [
        sha256,
        reference_kind,
        reference_id,
        round(reference_duration_s, 3),
        alignment_version,
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()
