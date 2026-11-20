"""A database written by the first service-mode release (#16) opens with the new store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.service.store import Store

OLD = """
CREATE TABLE uploads (upload_id TEXT PRIMARY KEY, cache_scope TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL, media TEXT NOT NULL, expires_at REAL NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE jobs (job_id TEXT PRIMARY KEY, cache_scope TEXT, client_ref TEXT,
    source_kind TEXT NOT NULL, source_ref TEXT, upload_id TEXT, fetch_url TEXT,
    status TEXT NOT NULL, outcome TEXT, failure_kind TEXT, retry_after_s REAL, analysis_id TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE analyses (analysis_id TEXT PRIMARY KEY, cache_scope TEXT, doc TEXT NOT NULL,
    created_at REAL NOT NULL);
INSERT INTO analyses VALUES ('an_old', 'a', '{}', 1.0);
"""


def snapshot(path: Path) -> tuple:
    with sqlite3.connect(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(analyses)")]
        idx = sorted(r[1] for r in conn.execute("PRAGMA index_list(analyses)"))
        tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master"))
    return cols, idx, tables


def test_an_old_database_gains_columns_and_indexes_and_reopening_is_a_no_op(tmp_path: Path) -> None:
    path = tmp_path / "svc.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD)
    Store(path)
    cols, idx, tables = snapshot(path)
    assert {"sha256", "fingerprint", "analyzer_version"} <= set(cols)
    assert "analyses_by_sha" in idx
    assert {"source_links", "alignments", "idempotency"} <= set(tables)
    Store(path)  # reopening changes nothing
    assert snapshot(path) == (cols, idx, tables)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT analysis_id FROM analyses").fetchall() == [("an_old",)]
