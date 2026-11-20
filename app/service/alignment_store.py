"""Storage side of the alignment cache (spotdown#11). The rules live in the pure
``alignment_doc`` and ``cache_policy``; this module only reads and writes rows.

One row per (scope, key): the key is the audio identity, the reference and ``alignment_version``
(``alignment_doc.cache_key``), and the scope is the analysis' own (``""`` for a global
``youtube`` analysis), so an alignment is shared exactly as its analysis is and is removed with
its scope.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import cache_policy as policy
from .alignment_doc import AlignedRow, build, cache_key, compose
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


def store_aligned(conn: sqlite3.Connection, aligning: Aligning, now: float) -> AlignedRow:
    """Inside the caller's transaction: the stored row for the key when it extends the same
    analysis (a concurrent job won), else this one. A row for an older analysis of the same
    audio (another analyzer version) is replaced."""
    scope_key = policy.scope_key(aligning.scope)
    found = conn.execute(
        "SELECT doc FROM alignments WHERE cache_scope = ? AND align_key = ?",
        (scope_key, aligning.key),
    ).fetchone()
    if found is not None:
        existing = _row(found["doc"])
        if existing is not None and existing.base_analysis_id == aligning.row.base_analysis_id:
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
                "SELECT cache_scope, doc FROM alignments WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            if found is None:
                return None
            row_scope = found["cache_scope"] or None
            row = _row(found["doc"])
            if row is None or not policy.readable_in(row_scope, scope):
                return None
            base = conn.execute(
                "SELECT cache_scope, doc FROM analyses WHERE analysis_id = ?",
                (row.base_analysis_id,),
            ).fetchone()
        if base is None or base["cache_scope"] != row_scope:
            return None
        return compose(json.loads(base["doc"]), row)
