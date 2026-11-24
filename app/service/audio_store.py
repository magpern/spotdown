"""Storage side of the mp3 route (``POST /v2/audio``): the request of each audio job and the kept
mp3 files (rules in ``audio_policy``; the files themselves in ``audio_files``).

An audio job is a row of ``jobs`` with ``source_kind`` ``audio``, no ``cache_scope`` and the video
id as ``source_ref``, plus its request here (bitrate, title, artists) and, once done, its
``audio_id``. A kept file is one row per (video id, bitrate). A job ends on a kept file in the
transaction that renews the file's expiry, so the sweep never deletes a file a job has just handed
out; a file a job is still making lives in that job's work directory, which the sweep keeps while
the job runs.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Set

from . import audio_policy
from .sqlite_base import SqliteBase

AUDIO_KIND = "audio"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_requests (
    job_id    TEXT PRIMARY KEY,
    video_id  TEXT NOT NULL,
    bitrate   INTEGER NOT NULL,
    title     TEXT NOT NULL,
    artists   TEXT NOT NULL,
    audio_id  TEXT
);
CREATE TABLE IF NOT EXISTS audio_files (
    audio_id    TEXT PRIMARY KEY,
    video_id    TEXT NOT NULL,
    bitrate     INTEGER NOT NULL,
    file_name   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS audio_files_source ON audio_files (video_id, bitrate, expires_at);
"""


def create(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _mark_done(conn: sqlite3.Connection, job_id: str, audio_id: str, now: float) -> bool:
    cur = conn.execute(
        "UPDATE jobs SET status = 'done', outcome = NULL, failure_kind = NULL, updated_at = ?"
        " WHERE job_id = ? AND status = 'running' AND source_kind = ?",
        (now, job_id, AUDIO_KIND),
    )
    if cur.rowcount != 1:
        return False
    conn.execute("UPDATE audio_requests SET audio_id = ? WHERE job_id = ?", (audio_id, job_id))
    return True


class AudioStore(SqliteBase):
    def add_audio_job(self, job_id: str, request: Dict[str, Any], now: float) -> None:
        """A queued audio job and its request, in one transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO jobs (job_id, cache_scope, source_kind, source_ref, status,"
                " created_at, updated_at) VALUES (?, NULL, ?, ?, 'queued', ?, ?)",
                (job_id, AUDIO_KIND, request["video_id"], now, now),
            )
            conn.execute(
                "INSERT INTO audio_requests (job_id, video_id, bitrate, title, artists)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    request["video_id"],
                    int(request["bitrate"]),
                    request["title"],
                    json.dumps(request["artists"]),
                ),
            )
            conn.execute("COMMIT")

    def delete_audio_job(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM jobs WHERE job_id = ? AND source_kind = ?", (job_id, AUDIO_KIND)
            )
            conn.execute("DELETE FROM audio_requests WHERE job_id = ?", (job_id,))

    def audio_request(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM audio_requests WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        found = dict(row)
        found["artists"] = json.loads(found["artists"])
        return found

    def finish_audio_reused(
        self, job_id: str, video_id: str, bitrate: int, now: float, ttl_s: float
    ) -> Optional[str]:
        """Ends the running job on a kept file of the same video and bitrate, renewing it; the
        file's id, or None (nothing changed) when no file is kept or the job is not running."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT audio_id FROM audio_files WHERE video_id = ? AND bitrate = ?"
                " AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (video_id, int(bitrate), now),
            ).fetchone()
            if row is None or not _mark_done(conn, job_id, row["audio_id"], now):
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE audio_files SET expires_at = ? WHERE audio_id = ?",
                (audio_policy.expires_at(now, ttl_s), row["audio_id"]),
            )
            conn.execute("COMMIT")
        return row["audio_id"]

    def finish_audio_done(
        self, job_id: str, file: Dict[str, Any], now: float, ttl_s: float
    ) -> bool:
        """Records a new kept file (already in place) and ends the running job on it. False
        (nothing stored) when the job is no longer running: the caller deletes the file."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO audio_files (audio_id, video_id, bitrate, file_name, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    file["audio_id"],
                    file["video_id"],
                    int(file["bitrate"]),
                    file["file_name"],
                    now,
                    audio_policy.expires_at(now, ttl_s),
                ),
            )
            if not _mark_done(conn, job_id, file["audio_id"], now):
                conn.execute("ROLLBACK")
                return False
            conn.execute("COMMIT")
        return True

    def kept_audio(self, audio_id: str, now: float) -> Optional[Dict[str, Any]]:
        """The kept file's row, or None when it is unknown or expired."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM audio_files WHERE audio_id = ?", (audio_id,)
            ).fetchone()
        if row is None or not audio_policy.is_kept(row["expires_at"], now):
            return None
        return dict(row)

    def audio_ids(self) -> Set[str]:
        with self._conn() as conn:
            return {r["audio_id"] for r in conn.execute("SELECT audio_id FROM audio_files")}

    def purge_expired_audio(self, now: float, record_ttl_s: float) -> List[str]:
        """Removes the rows of the expired files (returned, for the caller to unlink), the audio
        job records not running and not changed for ``record_ttl_s``, and requests whose job has
        gone."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            expired = [
                r["audio_id"]
                for r in conn.execute(
                    "SELECT audio_id FROM audio_files WHERE expires_at <= ?", (now,)
                )
            ]
            conn.executemany("DELETE FROM audio_files WHERE audio_id = ?", [(a,) for a in expired])
            conn.execute(
                "DELETE FROM jobs WHERE source_kind = ? AND status <> 'running'"
                " AND updated_at <= ?",
                (AUDIO_KIND, now - record_ttl_s),
            )
            conn.execute("DELETE FROM audio_requests WHERE job_id NOT IN (SELECT job_id FROM jobs)")
            conn.execute("COMMIT")
        return expired
