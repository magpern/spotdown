"""Storage side of the caches: lookups, source links, idempotency records and scope deletion
(the alignment cache is ``alignment_store``). The rules (keys, hit decision, windows) live in
the pure ``cache_policy`` and ``idempotency_policy``; this module only reads and writes rows.

Every query a caller can reach carries the caller's scope. Global entries (``cache_scope`` NULL,
scope key ``""``) are read by every scope and removed by none.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import cache_policy as policy
from . import idempotency_policy as idem
from .cache_policy import CacheEntry
from .sqlite_base import SqliteBase

_ENTRY_COLUMNS = "a.analysis_id, a.cache_scope, a.sha256, a.analyzer_version, a.fingerprint"


def _entry(row: sqlite3.Row) -> CacheEntry:
    return CacheEntry(
        analysis_id=row["analysis_id"],
        scope=row["cache_scope"],
        sha256=row["sha256"],
        analyzer_version=row["analyzer_version"],
        fingerprint=row["fingerprint"],
    )


def insert_analysis(
    conn: sqlite3.Connection,
    analysis_id: str,
    scope: Optional[str],
    doc: Dict[str, Any],
    now: float,
) -> None:
    """Stores an analysis with its cache index and, for a source with a reference, its link."""
    fields = policy.index_fields(doc)
    conn.execute(
        "INSERT INTO analyses (analysis_id, cache_scope, doc, created_at, sha256, fingerprint,"
        " analyzer_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            analysis_id,
            scope,
            json.dumps(doc, separators=(",", ":")),
            now,
            fields.sha256,
            fields.fingerprint,
            fields.analyzer_version,
        ),
    )
    put_link(conn, fields.source_kind, fields.source_ref, scope, analysis_id, now)


def put_link(
    conn: sqlite3.Connection,
    kind: str,
    ref: Optional[str],
    scope: Optional[str],
    analysis_id: str,
    now: float,
) -> None:
    if ref is None:  # an upload has no reference; its sha256 identifies the audio
        return
    key = policy.scope_key(policy.link_scope(kind, scope))
    conn.execute(
        "INSERT OR REPLACE INTO source_links (source_kind, source_ref, scope_key, analysis_id,"
        " created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, ref, key, analysis_id, now),
    )


class CacheStore(SqliteBase):
    # ---- lookups ----
    def entries_by_sha(self, scope: str, sha256: str) -> List[CacheEntry]:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM analyses a WHERE a.cache_scope = ? AND a.sha256 = ?",
                (scope, sha256),
            ).fetchall()
        return [_entry(r) for r in rows]

    def find_by_sha(self, scope: str, sha256: str, version: str) -> Optional[CacheEntry]:
        return policy.content_hit(
            self.entries_by_sha(scope, sha256), scope=scope, sha256=sha256, analyzer_version=version
        )

    def find_global_by_sha(self, sha256: str, version: str) -> Optional[CacheEntry]:
        """A global (``youtube``) analysis of these exact bytes, for reuse by a global source."""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM analyses a"
                " WHERE a.cache_scope IS NULL AND a.sha256 = ?",
                (sha256,),
            ).fetchall()
        hits = [e for e in map(_entry, rows) if policy.usable(e, None, version)]
        return hits[0] if hits else None

    def find_by_source(
        self, kind: str, ref: str, scope: Optional[str], version: str
    ) -> Optional[CacheEntry]:
        key = policy.scope_key(policy.link_scope(kind, scope))
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM source_links l JOIN analyses a"
                " ON a.analysis_id = l.analysis_id"
                " WHERE l.source_kind = ? AND l.source_ref = ? AND l.scope_key = ?",
                (kind, ref, key),
            ).fetchone()
        found = _entry(row) if row else None
        return policy.source_hit(found, kind=kind, cache_scope=scope, analyzer_version=version)

    def get_analysis_in_scope(self, analysis_id: str, scope: Optional[str]) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cache_scope, doc FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        if row is None or not policy.readable_in(row["cache_scope"], scope):
            return None
        return json.loads(row["doc"])

    # ---- idempotency ----
    def idem_get(self, scope: str, key: str) -> Optional[idem.Recorded]:
        now = time.time()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT request_hash, status, body, created_at FROM idempotency"
                " WHERE cache_scope = ? AND idem_key = ?",
                (scope, key),
            ).fetchone()
            if row is not None and idem.expired(row["created_at"], now):
                conn.execute(
                    "DELETE FROM idempotency WHERE cache_scope = ? AND idem_key = ?", (scope, key)
                )
                row = None
        if row is None:
            return None
        return idem.Recorded(row["request_hash"], row["status"], json.loads(row["body"]))

    def idem_put(self, scope: str, key: str, request_hash: str, status: int, body: Any) -> bool:
        """Records the first response for a key; False if a live record already exists.
        Records past the window are purged here, so the table does not grow without bound."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM idempotency WHERE created_at <= ?", (now - idem.WINDOW_S,))
            cur = conn.execute(
                "INSERT OR IGNORE INTO idempotency (cache_scope, idem_key, request_hash, status,"
                " body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (scope, key, request_hash, status, json.dumps(body), now),
            )
        return cur.rowcount == 1

    def idem_delete(self, scope: str, key: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM idempotency WHERE cache_scope = ? AND idem_key = ?", (scope, key)
            )

    # ---- scope deletion ----
    def delete_scope(self, scope: str) -> List[str]:
        """Removes everything held for ``scope``; returns its upload ids so the caller can delete
        the files. Global entries (scope NULL, scope key "") are never touched."""
        key = policy.scope_key(scope)
        if key == "":
            return []
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            upload_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT upload_id FROM uploads WHERE cache_scope = ?", (scope,)
                )
            ]
            conn.execute("DELETE FROM source_links WHERE scope_key = ?", (key,))
            # Defence in depth: no link of another scope key points to a scoped analysis today
            # (a global link always points to a global analysis), but none may outlive it.
            conn.execute(
                "DELETE FROM source_links WHERE analysis_id IN"
                " (SELECT analysis_id FROM analyses WHERE cache_scope = ?)",
                (scope,),
            )
            for table in ("analyses", "alignments", "idempotency", "jobs", "uploads"):
                conn.execute(f"DELETE FROM {table} WHERE cache_scope = ?", (scope,))
            conn.execute("COMMIT")
        return upload_ids
