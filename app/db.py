from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import get_settings

# One row per (track, format, bitrate) variant — so 320 and 128 are cached separately.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    variant_id   TEXT PRIMARY KEY,      -- "{track_id}-{format}-{bitrate}"
    track_id     TEXT NOT NULL,
    title        TEXT,
    artist       TEXT,
    file_name    TEXT NOT NULL,
    bitrate      INTEGER,
    format       TEXT,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_downloads_track_id ON downloads(track_id);

-- Standalone v1 analysis cache, keyed by (track_id, analyzer_version, response_version): tuning
-- the analyzer (which bumps its version) or changing the v1 response (which bumps
-- V1_RESPONSE_VERSION) refreshes instead of serving stale results (ADR 0001, Decision 2).
CREATE TABLE IF NOT EXISTS analyses (
    track_id         TEXT NOT NULL,
    analyzer_version INTEGER NOT NULL,
    response_version INTEGER NOT NULL,
    json             TEXT NOT NULL,
    created_at       INTEGER NOT NULL,
    PRIMARY KEY (track_id, analyzer_version, response_version)
);
"""


def variant_id(track_id: str, fmt: str, bitrate: int) -> str:
    return f"{track_id}-{fmt}-{bitrate}"


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Rebuild of an ``analyses`` table that has the older two-column key (ADR 0001, Decision 2). Every
# existing row is a version 1 response. One statement per entry: ``executescript`` commits first.
_REBUILD_STEPS = (
    """CREATE TABLE analyses_new (
           track_id         TEXT NOT NULL,
           analyzer_version INTEGER NOT NULL,
           response_version INTEGER NOT NULL,
           json             TEXT NOT NULL,
           created_at       INTEGER NOT NULL,
           PRIMARY KEY (track_id, analyzer_version, response_version)
       )""",
    """INSERT INTO analyses_new
           (track_id, analyzer_version, response_version, json, created_at)
       SELECT track_id, analyzer_version, 1, json, created_at FROM analyses""",
    "DROP TABLE analyses",
    "ALTER TABLE analyses_new RENAME TO analyses",
)


def _migrate_analyses() -> None:
    """Add ``response_version`` to the ``analyses`` key once, in one transaction.

    ``BEGIN IMMEDIATE`` takes the write lock first, so two processes starting together cannot both
    rebuild: the second waits, then finds the column inside its own transaction. SQLite DDL is
    transactional, so any failure rolls back and leaves the original table intact.
    """
    conn = sqlite3.connect(get_settings().db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(analyses)")]
            if "response_version" in columns:
                conn.execute("ROLLBACK")
                return
            for step in _REBUILD_STEPS:
                conn.execute(step)
            conn.execute("COMMIT")
        except BaseException:
            # Defensive: close() discards an open transaction anyway, so this is not tested.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    _migrate_analyses()


def get_done(vid: str) -> Optional[dict]:
    """Return a cached finished conversion for this exact variant whose file still exists."""
    settings = get_settings()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM downloads WHERE variant_id = ?", (vid,)).fetchone()
    if not row:
        return None
    if not (settings.files_dir / row["file_name"]).exists():
        return None
    return dict(row)


def mark_done(
    vid: str,
    track_id: str,
    title: str,
    artist: str,
    file_name: str,
    bitrate: int,
    fmt: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO downloads
               (variant_id, track_id, title, artist, file_name, bitrate, format, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (vid, track_id, title, artist, file_name, bitrate, fmt, int(time.time())),
        )


def get_best_file_for_track(track_id: str) -> Optional[str]:
    """Highest-bitrate converted file for this track (best source for analysis)."""
    settings = get_settings()
    with _conn() as conn:
        row = conn.execute(
            "SELECT file_name FROM downloads WHERE track_id = ? ORDER BY bitrate DESC LIMIT 1",
            (track_id,),
        ).fetchone()
    if not row:
        return None
    if not (settings.files_dir / row["file_name"]).exists():
        return None
    return row["file_name"]


def get_analysis(track_id: str, version: int, response_version: int) -> Optional[str]:
    """The cached v1 response for exactly this key; any other version pair is a miss."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT json FROM analyses"
            " WHERE track_id = ? AND analyzer_version = ? AND response_version = ?",
            (track_id, version, response_version),
        ).fetchone()
    return row["json"] if row else None


def set_analysis(track_id: str, version: int, response_version: int, json_str: str) -> None:
    """Write on the full key, so a row of another response version is never overwritten."""
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO analyses
               (track_id, analyzer_version, response_version, json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (track_id, version, response_version, json_str, int(time.time())),
        )
