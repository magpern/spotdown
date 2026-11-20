"""Tables of the caches, source links, alignments and idempotency records."""

from __future__ import annotations

import sqlite3

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id      TEXT PRIMARY KEY,
    cache_scope      TEXT,
    doc              TEXT NOT NULL,
    created_at       REAL NOT NULL,
    sha256           TEXT,
    fingerprint      TEXT,
    analyzer_version TEXT
);
CREATE TABLE IF NOT EXISTS source_links (
    source_kind TEXT NOT NULL,
    source_ref  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (source_kind, source_ref, scope_key)
);
CREATE TABLE IF NOT EXISTS alignments (
    cache_scope TEXT NOT NULL,
    align_key   TEXT NOT NULL,
    doc         TEXT NOT NULL,
    created_at  REAL NOT NULL,
    analysis_id TEXT,
    PRIMARY KEY (cache_scope, align_key)
);
CREATE TABLE IF NOT EXISTS idempotency (
    cache_scope  TEXT NOT NULL,
    idem_key     TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status       INTEGER NOT NULL,
    body         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (cache_scope, idem_key)
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS analyses_by_sha ON analyses (cache_scope, sha256);
CREATE INDEX IF NOT EXISTS jobs_by_scope ON jobs (cache_scope);
CREATE INDEX IF NOT EXISTS uploads_by_scope ON uploads (cache_scope);
CREATE UNIQUE INDEX IF NOT EXISTS alignments_by_id ON alignments (analysis_id);
"""

_ANALYSES_COLUMNS = ("sha256", "fingerprint", "analyzer_version")


def create(conn: sqlite3.Connection) -> None:
    """Creates the tables and adds the columns an older table lacks: the index columns of an
    ``analyses`` table of the first service-mode release (spotdown#4), and the aligned analysis
    id of an ``alignments`` table of spotdown#5."""
    conn.executescript(CACHE_SCHEMA)
    have = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
    for column in _ANALYSES_COLUMNS:
        if column not in have:
            conn.execute(f"ALTER TABLE analyses ADD COLUMN {column} TEXT")
    # The aligned analysis id (spotdown#11); an alignments table of spotdown#5 lacks it.
    if "analysis_id" not in {row[1] for row in conn.execute("PRAGMA table_info(alignments)")}:
        conn.execute("ALTER TABLE alignments ADD COLUMN analysis_id TEXT")
    conn.executescript(_INDEXES)
