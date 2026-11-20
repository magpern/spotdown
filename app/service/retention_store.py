"""Storage side of the retention of global entries (``retention_policy``, ADR 0040 §7): the read
renewal, the purge of unused or outdated analyses and the takedown purge of one source ref.

An analysis entry is the analysis row, the source links that point to it, the global alignments
built on it and the match record inside its document. Deleting one removes all of them in one
transaction; the connection overwrites the freed pages (``secure_delete``) and truncates the
write-ahead log afterwards, so a purged document is not left in the database files. Only rows
with ``cache_scope`` NULL (global) are ever selected; a scoped analysis is never touched.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Dict, Iterable, List

from . import retention_policy as policy
from .sqlite_base import SqliteBase

log = logging.getLogger("spotdown.service.retention")


def touch_global(conn: sqlite3.Connection, analysis_id: str, now: float) -> None:
    """Renews a global analysis' last read, unless it was renewed within the touch interval."""
    row = conn.execute(
        "SELECT last_read_at FROM analyses WHERE analysis_id = ? AND cache_scope IS NULL",
        (analysis_id,),
    ).fetchone()
    if row is not None and (row[0] is None or policy.touch_due(row[0], now)):
        conn.execute(
            "UPDATE analyses SET last_read_at = ? WHERE analysis_id = ? AND cache_scope IS NULL",
            (now, analysis_id),
        )


def _unused_global_ids(
    conn: sqlite3.Connection, now: float, ttl_s: float, current_version: str
) -> List[str]:
    """Ids of the GLOBAL analyses (``cache_scope`` NULL) due for purge; never a scoped one."""
    rows = conn.execute(
        "SELECT analysis_id, last_read_at, analyzer_version, created_at FROM analyses"
        " WHERE cache_scope IS NULL"
    ).fetchall()
    return [
        r["analysis_id"]
        for r in rows
        if policy.purge_due(
            r["last_read_at"] if r["last_read_at"] is not None else r["created_at"],
            r["analyzer_version"],
            now=now,
            ttl_s=ttl_s,
            current=current_version,
        )
    ]


def _delete_entries(conn: sqlite3.Connection, ids: Iterable[str]) -> Dict[str, int]:
    """Deletes the global analyses ``ids`` with their global links and global alignments. Each
    statement carries its own global condition (link scope key '', alignment scope '', analysis
    scope NULL), so an id of a scoped analysis reaches no scoped row, whatever the caller passed."""
    counts = {"analyses": 0, "links": 0, "alignments": 0}
    for analysis_id in ids:
        counts["links"] += conn.execute(
            "DELETE FROM source_links WHERE analysis_id = ? AND scope_key = ''",
            (analysis_id,),
        ).rowcount
        counts["alignments"] += conn.execute(
            "DELETE FROM alignments WHERE cache_scope = '' AND (analysis_id = ?"
            " OR json_extract(doc, '$.base_analysis_id') = ?)",
            (analysis_id, analysis_id),
        ).rowcount
        counts["analyses"] += conn.execute(
            "DELETE FROM analyses WHERE analysis_id = ? AND cache_scope IS NULL", (analysis_id,)
        ).rowcount
    return counts


def _dangling_links(conn: sqlite3.Connection) -> int:
    """Global links whose analysis is gone (no code path leaves one; purged for certain)."""
    return conn.execute(
        "DELETE FROM source_links WHERE scope_key = '' AND analysis_id NOT IN"
        " (SELECT analysis_id FROM analyses)"
    ).rowcount


class RetentionStore(SqliteBase):
    def _begin_purge(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA secure_delete = ON")  # freed pages are overwritten, not just freed
        conn.execute("BEGIN IMMEDIATE")

    def _end_purge(self, conn: sqlite3.Connection, deleted: int) -> None:
        """Commits; when something was deleted, truncates the write-ahead log so the deleted
        document is not left in it. A busy checkpoint (a reader holds the log) is logged, never
        raised: the rows are deleted either way and the next purge truncates it."""
        conn.execute("COMMIT")
        if deleted == 0:
            return
        conn.execute("PRAGMA busy_timeout = 0")  # a reader makes this busy now, not after 30 s
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy is not None and busy[0]:
            log.warning("retention: the write-ahead log could not be truncated (a reader is busy)")

    def purge_unused_analyses(
        self, now: float, current_version: str, ttl_s: float
    ) -> Dict[str, int]:
        """Purges the global analyses not read for ``ttl_s`` or of another analyzer version.
        Idempotent: a second run finds nothing."""
        with self._conn() as conn:
            self._begin_purge(conn)
            victims = _unused_global_ids(conn, now, ttl_s, current_version)
            counts = _delete_entries(conn, victims)
            counts["links"] += _dangling_links(conn)
            self._end_purge(conn, sum(counts.values()))
        return counts

    def takedown(self, kind: str, ref: str) -> Dict[str, int]:
        """Removes everything held for one global source ref: its analyses (found by link and by
        the source in their document), the links to them, their alignments and the match records
        of the video. Returns counts only. Idempotent."""
        with self._conn() as conn:
            self._begin_purge(conn)
            victims = {
                r[0]
                for r in conn.execute(
                    "SELECT analysis_id FROM source_links WHERE source_kind = ? AND source_ref = ?"
                    " AND scope_key = ''",
                    (kind, ref),
                )
            } | {
                r[0]
                for r in conn.execute(
                    "SELECT analysis_id FROM analyses WHERE cache_scope IS NULL"
                    " AND json_extract(doc, '$.provenance.source.kind') = ?"
                    " AND json_extract(doc, '$.provenance.source.ref') = ?",
                    (kind, ref),
                )
            }
            counts = _delete_entries(conn, sorted(victims))
            counts["links"] += conn.execute(
                "DELETE FROM source_links WHERE source_kind = ? AND source_ref = ?"
                " AND scope_key = ''",
                (kind, ref),
            ).rowcount
            counts["match_records"] = (
                conn.execute("DELETE FROM match_choices WHERE video_id = ?", (ref,)).rowcount
                + conn.execute(
                    "DELETE FROM match_cache WHERE EXISTS (SELECT 1 FROM json_each(entries)"
                    " WHERE json_extract(value, '$.id') = ?)",
                    (ref,),
                ).rowcount
            )
            self._end_purge(conn, sum(counts.values()))
        return counts
