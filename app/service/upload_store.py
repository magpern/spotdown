"""Storage of the temporary uploads (``PUT /v2/uploads``): one row per upload, no file name.

The bytes live in a temporary file (``uploads.upload_path``), deleted after the job that used the
upload or once its TTL passes; this module only reads and writes the rows.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from .sqlite_base import SqliteBase

UPLOADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    upload_id   TEXT PRIMARY KEY,
    cache_scope TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    media       TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    consumed    INTEGER NOT NULL DEFAULT 0
);
"""


def create(conn: sqlite3.Connection) -> None:
    conn.executescript(UPLOADS_SCHEMA)


class UploadStore(SqliteBase):
    def add_upload(
        self, upload_id: str, scope: str, sha256: str, size: int, media: str, ttl_s: float
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO uploads (upload_id, cache_scope, sha256, bytes, media, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (upload_id, scope, sha256, size, media, time.time() + ttl_s),
            )

    def claim_upload(self, upload_id: str, scope: str) -> Optional[Dict[str, Any]]:
        """Mark an unexpired upload of ``scope`` as used by one job; None if there is none."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE uploads SET consumed = 1 WHERE upload_id = ? AND cache_scope = ?"
                " AND consumed = 0 AND expires_at > ?",
                (upload_id, scope, time.time()),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,))
            found = row.fetchone()
        return dict(found) if found else None

    def release_upload(self, upload_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE uploads SET consumed = 0 WHERE upload_id = ?", (upload_id,))

    def get_upload(self, upload_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,))
            found = row.fetchone()
        return dict(found) if found else None

    def delete_upload(self, upload_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM uploads WHERE upload_id = ?", (upload_id,))

    def stale_uploads(self, now: float, grace_s: float) -> List[str]:
        """Unused uploads past their TTL, and any upload ``grace_s`` past it (a lost job); never
        the upload of a running job (the stale-job sweep removes it once the job's lease ends)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT upload_id FROM uploads WHERE ((consumed = 0 AND expires_at <= ?)"
                " OR expires_at <= ?) AND upload_id NOT IN (SELECT upload_id FROM jobs"
                " WHERE status = 'running' AND upload_id IS NOT NULL)",
                (now, now - grace_s),
            ).fetchall()
        return [r["upload_id"] for r in rows]
