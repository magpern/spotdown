"""Storage of the global matcher state: the source-match cache, the provider backoff and the
match record of each chosen video. All of it is global (public search results, no scope): it is
never keyed on, and never holds, a ``cache_scope``, a ``client_ref`` or a job id.

A match record holds the requested (Spotify-catalogue) title, artists and duration, so it lives
``GLOBAL_REFERENCE_TTL_S`` (ADR 0040 §7): an expired record is never read and is purged when a
record is next stored. Within its lifetime the latest match that chose a video wins.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .sqlite_base import SqliteBase

MATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_cache (
    fingerprint TEXT PRIMARY KEY,
    entries     TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_backoff (
    provider TEXT PRIMARY KEY,
    until    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS match_choices (
    video_id   TEXT PRIMARY KEY,
    record     TEXT NOT NULL,
    duration_s REAL NOT NULL,
    created_at REAL NOT NULL
);
"""


def create(conn: sqlite3.Connection) -> None:
    conn.executescript(MATCH_SCHEMA)


class MatchStore(SqliteBase):
    # ---- source-match cache ----
    def match_get(self, fingerprint: str, now: float) -> Optional[List[Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT entries, expires_at FROM match_cache WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if row is None or row["expires_at"] <= now:
            return None
        return json.loads(row["entries"])

    def match_put(self, fingerprint: str, entries: List[Any], now: float, ttl_s: float) -> None:
        """Stores the search entries; expired entries are purged here so the table stays small."""
        with self._conn() as conn:
            conn.execute("DELETE FROM match_cache WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT OR REPLACE INTO match_cache (fingerprint, entries, expires_at)"
                " VALUES (?, ?, ?)",
                (fingerprint, json.dumps(entries, separators=(",", ":")), now + ttl_s),
            )

    # ---- provider backoff (rate limits) ----
    def backoff_until(self, provider: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT until FROM provider_backoff WHERE provider = ?", (provider,)
            ).fetchone()
        return float(row["until"]) if row else 0.0

    def set_backoff(self, provider: str, until: float) -> None:
        """Extends the backoff to ``until``; never shortens a longer one already set."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO provider_backoff (provider, until) VALUES (?, ?)"
                " ON CONFLICT(provider) DO UPDATE SET until = MAX(until, excluded.until)",
                (provider, until),
            )

    # ---- match record of a chosen video (for provenance) ----
    def put_choice(
        self, video_id: str, record: Dict[str, Any], duration_s: float, now: float
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM match_choices WHERE created_at <= ?", (now - self.global_ttl_s,)
            )
            conn.execute(
                "INSERT OR REPLACE INTO match_choices (video_id, record, duration_s, created_at)"
                " VALUES (?, ?, ?, ?)",
                (video_id, json.dumps(record, separators=(",", ":")), duration_s, now),
            )

    def get_choice(self, video_id: str, now: float) -> Optional[Tuple[Dict[str, Any], float]]:
        """The latest unexpired match record that chose ``video_id``, with the requested
        duration."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT record, duration_s FROM match_choices"
                " WHERE video_id = ? AND created_at > ?",
                (video_id, now - self.global_ttl_s),
            ).fetchone()
        return (json.loads(row["record"]), float(row["duration_s"])) if row else None
