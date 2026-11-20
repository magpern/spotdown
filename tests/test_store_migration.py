"""A database written by the first service-mode release (#16) opens with the new store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.service import analysis_doc, url_policy
from app.service.store import Store

from .service_helpers import fake_analyser_result

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


def _doc(analysis_id: str, sha256: str, source: dict) -> str:
    result = fake_analyser_result()
    doc = analysis_doc.build(
        core=result["core"],
        probe=result["probe"],
        components=result["components"],
        sha256=sha256,
        source=source,
        job_id="job_old",
        analysis_id=analysis_id,
        timings_s={},
        created_at="2026-11-01T00:00:00Z",
    )
    return json.dumps(doc)


def test_analyses_of_an_old_database_are_backfilled_and_become_hits(tmp_path: Path) -> None:
    path = tmp_path / "svc.sqlite"
    version = analysis_doc.ANALYZER_VERSION_V2
    url_ref = url_policy.url_ref("media.example.com", "/a.wav")
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD)
        conn.execute(
            "INSERT INTO analyses VALUES ('an_up', 'a', ?, 2.0)",
            (_doc("an_up", "a" * 64, {"kind": "upload"}),),
        )
        conn.execute(
            "INSERT INTO analyses VALUES ('an_url', 'a', ?, 3.0)",
            (_doc("an_url", "b" * 64, {"kind": "url", "ref": url_ref}),),
        )
        conn.execute("INSERT INTO analyses VALUES ('an_bad', 'a', '[1]', 4.0)")
    store = Store(path)
    hit = store.find_by_sha("a", "a" * 64, version)
    assert hit is not None and hit.analysis_id == "an_up"
    assert store.find_by_sha("b", "a" * 64, version) is None  # still only in its scope
    found = store.find_by_source("url", url_ref, "a", version)
    assert found is not None and found.analysis_id == "an_url"
    assert store.find_by_source("url", url_ref, "b", version) is None
    with sqlite3.connect(path) as conn:
        left = conn.execute("SELECT analysis_id FROM analyses WHERE sha256 IS NULL").fetchall()
    assert sorted(left) == [("an_bad",), ("an_old",)]  # unreadable documents stay misses
    Store(path)  # reopening is a no-op
    assert store.find_by_sha("a", "a" * 64, version) == hit


def test_the_backfill_runs_once_and_links_no_old_form_url(tmp_path: Path) -> None:
    path = tmp_path / "svc.sqlite"
    version = analysis_doc.ANALYZER_VERSION_V2
    old_ref = "https://media.example.com/a.wav"  # the host-and-path ref of that release
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD)
        conn.execute(
            "INSERT INTO analyses VALUES ('an_oldurl', 'a', ?, 2.0)",
            (_doc("an_oldurl", "c" * 64, {"kind": "url", "ref": old_ref}),),
        )
    store = Store(path)
    found = store.find_by_sha("a", "c" * 64, version)
    assert found is not None and found.analysis_id == "an_oldurl"  # found by its bytes
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 0
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
        conn.execute("UPDATE analyses SET sha256 = NULL WHERE analysis_id = 'an_oldurl'")
    Store(path)  # already migrated: no second backfill
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT sha256 FROM analyses WHERE analysis_id = 'an_oldurl'")
        assert row.fetchone()[0] is None
