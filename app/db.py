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


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def get_done(vid: str) -> Optional[dict]:
    """Return a cached finished conversion for this exact variant whose file still exists."""
    settings = get_settings()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM downloads WHERE variant_id = ?", (vid,)
        ).fetchone()
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
