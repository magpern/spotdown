"""Storage side of the retention of scoped entries (spotdown#40; rules in ``retention_policy``).

A scope that is never deleted (classbeat's shared studio scope, for example) would otherwise keep
everything for ever. So a scoped analysis is purged by the same rules as a global one: not read
for ``SCOPED_RETENTION_TTL_S`` (default 90 days), or of another ``analyzer_version`` than the
running one (it can never be a hit again). It goes with the source links and alignments of its
own scope. Scoped job records not changed for the same period, and idempotency records past
their window, go too.

Each statement is bound to a scope: only rows with a non-empty ``cache_scope`` (scope key) are
selected here, so a global row is never touched (it has its own purge, ``retention_store``). A
running job's record is never deleted, and an analysis a running job reuses is renewed when the
job finds it (``find_by_sha``) and checked again when the job ends on it
(``Store.finish_job_reused``), so a purge never leaves a job done on a missing analysis.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple

from . import idempotency_policy as idem
from . import retention_policy as policy
from .retention_store import BASE_ID, RetentionStore


def _unused_scoped_ids(
    conn: sqlite3.Connection, now: float, ttl_s: float, current_version: str
) -> List[Tuple[str, str]]:
    """``(analysis_id, scope)`` of the SCOPED analyses due for purge; never a global one."""
    rows = conn.execute(
        "SELECT analysis_id, cache_scope, last_read_at, analyzer_version, created_at"
        " FROM analyses WHERE cache_scope IS NOT NULL AND cache_scope <> ''"
    ).fetchall()
    return [
        (r["analysis_id"], r["cache_scope"])
        for r in rows
        if policy.purge_due(
            r["last_read_at"] if r["last_read_at"] is not None else r["created_at"],
            r["analyzer_version"],
            now=now,
            ttl_s=ttl_s,
            current=current_version,
        )
    ]


def _delete_scoped_entries(
    conn: sqlite3.Connection, victims: Iterable[Tuple[str, Optional[str]]]
) -> Dict[str, int]:
    """Deletes each scoped analysis with the links and alignments of its own scope. Every
    statement carries that scope, and an empty scope (global) is skipped, so no id reaches a
    global row or a row of another scope, whatever the caller passed."""
    counts = {"analyses": 0, "links": 0, "alignments": 0}
    for analysis_id, scope in victims:
        if not scope:
            continue
        counts["links"] += conn.execute(
            "DELETE FROM source_links WHERE analysis_id = ? AND scope_key = ?",
            (analysis_id, scope),
        ).rowcount
        counts["alignments"] += conn.execute(
            "DELETE FROM alignments WHERE cache_scope = ? AND (analysis_id = ? OR "
            + BASE_ID
            + " = ?)",
            (scope, analysis_id, analysis_id),
        ).rowcount
        counts["analyses"] += conn.execute(
            "DELETE FROM analyses WHERE analysis_id = ? AND cache_scope = ?", (analysis_id, scope)
        ).rowcount
    return counts


def _dangling_scoped(conn: sqlite3.Connection) -> Tuple[int, int]:
    """Scoped links and scoped alignments whose analysis is gone (none is served; no code path
    leaves one today, but none may outlive its analysis). A scoped alignment whose document is
    not valid JSON can never be served (its read fails) and names no analysis, so it goes too;
    no document is ever parsed unchecked, so one malformed row never makes the purge raise."""
    links = conn.execute(
        "DELETE FROM source_links WHERE scope_key <> '' AND analysis_id NOT IN"
        " (SELECT analysis_id FROM analyses)"
    ).rowcount
    alignments = conn.execute(
        "DELETE FROM alignments WHERE cache_scope <> '' AND CASE WHEN json_valid(doc)"
        " THEN json_extract(doc, '$.base_analysis_id') NOT IN (SELECT analysis_id FROM analyses)"
        " ELSE 1 END"
    ).rowcount
    return links, alignments


class ScopeRetentionStore(RetentionStore):
    def purge_scoped(self, now: float, current_version: str, ttl_s: float) -> Dict[str, int]:
        """Purges the scoped analyses not read for ``ttl_s`` or of another analyzer version (with
        their links and alignments), the scoped job records not running and not changed for
        ``ttl_s``, and the idempotency records past their window. Counts only; idempotent."""
        with self._conn() as conn:
            self._begin_purge(conn)
            victims = _unused_scoped_ids(conn, now, ttl_s, current_version)
            counts = _delete_scoped_entries(conn, victims)
            links, alignments = _dangling_scoped(conn)
            counts["links"] += links
            counts["alignments"] += alignments
            counts["jobs"] = conn.execute(
                "DELETE FROM jobs WHERE cache_scope IS NOT NULL AND cache_scope <> ''"
                " AND status <> 'running' AND updated_at <= ?",
                (now - ttl_s,),
            ).rowcount
            # Past its window a record is never served (``idem_get``) in any scope.
            counts["idempotency"] = conn.execute(
                "DELETE FROM idempotency WHERE created_at <= ?", (now - idem.WINDOW_S,)
            ).rowcount
            self._end_purge(conn, sum(counts.values()))
        return counts
