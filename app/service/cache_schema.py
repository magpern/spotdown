"""Tables of the caches, source links, alignments and idempotency records."""

from __future__ import annotations

import json
import sqlite3
import time

from . import cache_policy

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id      TEXT PRIMARY KEY,
    cache_scope      TEXT,
    doc              TEXT NOT NULL,
    created_at       REAL NOT NULL,
    sha256           TEXT,
    fingerprint      TEXT,
    analyzer_version TEXT,
    last_read_at     REAL
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
# PRAGMA user_version once the backfill of spotdown#4 analyses has run.
SCHEMA_VERSION = 1


def create(conn: sqlite3.Connection) -> None:
    """Creates the tables and adds the columns an older table lacks: the index columns of an
    ``analyses`` table of the first service-mode release (spotdown#4), backfilled from each
    document, and the aligned analysis id of an ``alignments`` table of spotdown#5."""
    conn.executescript(CACHE_SCHEMA)
    have = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
    for column in _ANALYSES_COLUMNS:
        if column not in have:
            conn.execute(f"ALTER TABLE analyses ADD COLUMN {column} TEXT")
    if "last_read_at" not in have:  # retention (ADR 0040 §7): an older row counts from creation
        conn.execute("ALTER TABLE analyses ADD COLUMN last_read_at REAL")
        conn.execute("UPDATE analyses SET last_read_at = created_at WHERE last_read_at IS NULL")
    # The aligned analysis id (spotdown#11); an alignments table of spotdown#5 lacks it.
    if "analysis_id" not in {row[1] for row in conn.execute("PRAGMA table_info(alignments)")}:
        conn.execute("ALTER TABLE alignments ADD COLUMN analysis_id TEXT")
    conn.executescript(_INDEXES)
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _backfill(conn)  # once per database, not on every store construction
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _backfill(conn: sqlite3.Connection) -> None:
    """Fills the index columns, and the source link, of analyses stored before they existed
    (spotdown#4), so they are cache hits and lookup results. A row whose document cannot be
    read is left as it is (it stays a miss, never a wrong hit)."""
    rows = conn.execute(
        "SELECT analysis_id, cache_scope, doc FROM analyses WHERE sha256 IS NULL"
    ).fetchall()
    now = time.time()
    for analysis_id, scope, doc in rows:
        try:
            fields = cache_policy.index_fields(json.loads(doc))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        conn.execute(
            "UPDATE analyses SET sha256 = ?, fingerprint = ?, analyzer_version = ?"
            " WHERE analysis_id = ?",
            (fields.sha256, fields.fingerprint, fields.analyzer_version, analysis_id),
        )
        # A url ref of that release is host and path; lookups now use the digest form, so such
        # a link could never match: the analysis is found by its sha256 only.
        if fields.source_ref is not None and not (
            fields.source_kind == "url" and not fields.source_ref.startswith("sha256:")
        ):
            key = cache_policy.scope_key(cache_policy.link_scope(fields.source_kind, scope))
            conn.execute(
                "INSERT OR IGNORE INTO source_links (source_kind, source_ref, scope_key,"
                " analysis_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (fields.source_kind, fields.source_ref, key, analysis_id, now),
            )
