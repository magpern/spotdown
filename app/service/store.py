"""Service-mode records in SQLite: uploads, jobs and the analyses they produced.

No audio is kept here (upload bytes live in a temporary file deleted after their job or TTL), no
file name, no key and no URL beyond the job's own run: ``fetch_url`` is cleared when the job ends.
Every read that a caller can reach is filtered by its ``cache_scope``. The caches and the scope
deletion are spotdown#5 and build on these tables.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    upload_id   TEXT PRIMARY KEY,
    cache_scope TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    media       TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    consumed    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    cache_scope   TEXT,
    client_ref    TEXT,
    source_kind   TEXT NOT NULL,
    source_ref    TEXT,
    upload_id     TEXT,
    fetch_url     TEXT,
    status        TEXT NOT NULL,
    outcome       TEXT,
    failure_kind  TEXT,
    retry_after_s REAL,
    analysis_id   TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    cache_scope TEXT,
    doc         TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---- uploads ----
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
        """Unused uploads past their TTL, and any upload ``grace_s`` past it (a lost job)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT upload_id FROM uploads WHERE (consumed = 0 AND expires_at <= ?)"
                " OR expires_at <= ?",
                (now, now - grace_s),
            ).fetchall()
        return [r["upload_id"] for r in rows]

    # ---- jobs ----
    def add_job(self, job: Dict[str, Any]) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, cache_scope, client_ref, source_kind, source_ref,"
                " upload_id, fetch_url, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    job["job_id"],
                    job.get("cache_scope"),
                    job.get("client_ref"),
                    job["source_kind"],
                    job.get("source_ref"),
                    job.get("upload_id"),
                    job.get("fetch_url"),
                    now,
                    now,
                ),
            )

    def delete_job(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_job_in_scope(self, job_id: str, scope: Optional[str]) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ? AND cache_scope IS ?", (job_id, scope)
            ).fetchone()
        return dict(row) if row else None

    def start_job(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ?"
                " WHERE job_id = ? AND status = 'queued'",
                (time.time(), job_id),
            )
        return cur.rowcount == 1

    def finish_job_done(
        self, job_id: str, analysis_id: str, scope: Optional[str], doc: Any
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO analyses (analysis_id, cache_scope, doc, created_at)"
                " VALUES (?, ?, ?, ?)",
                (analysis_id, scope, json.dumps(doc, separators=(",", ":")), now),
            )
            conn.execute(
                "UPDATE jobs SET status = 'done', outcome = NULL, failure_kind = NULL,"
                " analysis_id = ?, fetch_url = NULL, updated_at = ? WHERE job_id = ?",
                (analysis_id, now, job_id),
            )
            conn.execute("COMMIT")

    def finish_job_failed(
        self,
        job_id: str,
        outcome: str,
        failure_kind: Optional[str],
        retry_after_s: Optional[float],
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', outcome = ?, failure_kind = ?,"
                " retry_after_s = ?, analysis_id = NULL, fetch_url = NULL, updated_at = ?"
                " WHERE job_id = ?",
                (outcome, failure_kind, retry_after_s, time.time(), job_id),
            )

    def clear_fetch_url(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET fetch_url = NULL WHERE job_id = ?", (job_id,))

    # ---- analyses ----
    def get_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT doc FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        return json.loads(row["doc"]) if row else None
