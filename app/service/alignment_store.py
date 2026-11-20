"""Storage side of the alignment cache (spotdown#11). The rules live in the pure
``alignment_doc`` and ``cache_policy``; this module only reads and writes rows.

One row per (scope, key): the key is the audio identity, the reference and ``alignment_version``
(``alignment_doc.cache_key``), and the scope is the analysis' own (``""`` for a global
``youtube`` analysis), so an alignment is shared exactly as its analysis is and is removed with
its scope. A global row holds a reference (a Spotify-catalogue id for a Spotify reference) that no
scope deletion removes, so it lives ``GLOBAL_REFERENCE_TTL_S`` (ADR 0040 §7): past that it is
never served and is purged when an alignment is next stored.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import cache_policy as policy
from .alignment_doc import AlignedRow, build, cache_key, compose
from .retention_store import touch_global
from .sqlite_base import SqliteBase


@dataclass(frozen=True)
class Aligning:
    """An alignment to store with a job: the analysis' scope, the cache key and the row."""

    scope: Optional[str]
    key: str
    row: AlignedRow


def aligning_for(
    base_doc: Dict[str, Any], scope: Optional[str], reference: Dict[str, Any]
) -> Aligning:
    """A fresh alignment of ``base_doc`` (stored in ``scope``, the analysis' own) to
    ``reference``. Storing it reuses a cached row for the same key and analysis instead."""
    row = build(base_doc, reference, "an_" + uuid.uuid4().hex)
    return Aligning(scope, cache_key(base_doc, reference), row)


def store_aligned(
    conn: sqlite3.Connection, aligning: Aligning, now: float, global_ttl_s: float
) -> AlignedRow:
    """Inside the caller's transaction: the stored row for the key when it still serves (a
    concurrent job won, or another analysis of the same bytes at the same analyzer version
    in the same scope holds it), else this one. A row whose analysis is gone or is of another
    analyzer version is replaced, and so is an expired global row. So two analyses of the same
    bytes never flip the row, and an aligned id a job ended on stays readable."""
    scope_key = policy.scope_key(aligning.scope)
    conn.execute(
        "DELETE FROM alignments WHERE cache_scope = '' AND created_at <= ?",
        (now - global_ttl_s,),
    )
    found = conn.execute(
        "SELECT doc FROM alignments WHERE cache_scope = ? AND align_key = ?",
        (scope_key, aligning.key),
    ).fetchone()
    existing = _row(found["doc"]) if found is not None else None
    if existing is not None and _still_serves(conn, existing, aligning):
        return existing
    conn.execute(
        "INSERT OR REPLACE INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            scope_key,
            aligning.key,
            json.dumps(aligning.row.as_doc(), separators=(",", ":")),
            now,
            aligning.row.analysis_id,
        ),
    )
    return aligning.row


def _still_serves(conn: sqlite3.Connection, existing: AlignedRow, aligning: Aligning) -> bool:
    new_base = aligning.row.base_analysis_id
    if existing.base_analysis_id == new_base:
        return True
    rows = conn.execute(
        "SELECT analysis_id, cache_scope, analyzer_version FROM analyses"
        " WHERE analysis_id IN (?, ?)",
        (existing.base_analysis_id, new_base),
    ).fetchall()
    found = {r["analysis_id"]: r for r in rows}
    old, new = found.get(existing.base_analysis_id), found.get(new_base)
    return (
        old is not None
        and new is not None
        and old["cache_scope"] == aligning.scope
        and old["analyzer_version"] == new["analyzer_version"]
    )


def _row(doc: str) -> Optional[AlignedRow]:
    try:
        return AlignedRow.from_doc(json.loads(doc))
    except (KeyError, TypeError, ValueError):
        return None  # not an aligned row (a hand-written cache entry): never served


class AlignmentStore(SqliteBase):
    def get_aligned_in_scope(
        self, analysis_id: str, scope: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """An aligned analysis readable in ``scope`` (its own scope, or a global one), composed
        from its file-clock analysis; None when either is missing or not readable."""
        with self._conn() as conn:
            found = conn.execute(
                "SELECT cache_scope, doc, created_at FROM alignments WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
            if found is None:
                return None
            row_scope = found["cache_scope"] or None
            if row_scope is None and policy.expired_global(
                found["created_at"], self.clock(), self.global_ttl_s
            ):
                return None
            row = _row(found["doc"])
            if row is None or not policy.readable_in(row_scope, scope):
                return None
            base = conn.execute(
                "SELECT cache_scope, doc FROM analyses WHERE analysis_id = ?",
                (row.base_analysis_id,),
            ).fetchone()
            if base is None or base["cache_scope"] != row_scope:
                return None
            if row_scope is None:  # reading a global alignment is a read of its analysis
                touch_global(conn, row.base_analysis_id, self.clock())
        return compose(json.loads(base["doc"]), row)
