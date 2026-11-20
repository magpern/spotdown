"""``DELETE /v2/scopes/{cache_scope}`` removes everything of the scope and nothing else."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.service.cache_store import insert_analysis

from .cache_helpers import URL, Stack, sha
from .service_helpers import AUTH, SCOPE, audio_files

SCOPED = {
    "analyses": "cache_scope",
    "jobs": "cache_scope",
    "uploads": "cache_scope",
    "alignments": "cache_scope",
    "idempotency": "cache_scope",
}


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def count(stack: Stack, table: str, column: str, scope: str) -> int:
    return len(stack.rows(table, column, scope))


def fill(stack: Stack, scope: str) -> bytes:
    data = stack.audio("a.wav")
    stack.fetch_bytes = stack.audio("u.wav", 3.0)
    stack.analysis_id(stack.analyse_bytes(data, scope), scope)
    stack.analysis_id(stack.post({"cache_scope": scope, "url": URL}), scope)
    stack.upload(stack.audio("c.wav", 4.0), scope)  # an unused upload, still pending
    stack.store.put_alignment(scope, "k", {"offset_s": 1.0})
    return data


def test_scope_deletion_leaves_nothing_of_that_scope(stack: Stack) -> None:
    data = fill(stack, "scope_a")
    keep = fill(stack, "scope_b")
    for table, col in SCOPED.items():
        assert count(stack, table, col, "scope_a") > 0, table
    with stack.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 2
    # a global entry that must survive
    doc = stack.store.get_analysis(stack.lookup("scope_b", sha256=sha(keep)).json()["analysis_id"])
    assert doc is not None
    doc["provenance"]["source"] = {"kind": "youtube", "ref": "dQw4w9WgXcQ"}
    with stack.store._conn() as conn:
        insert_analysis(conn, "an_global", None, doc, 1.0)

    res = stack.client.delete("/v2/scopes/scope_a", headers=AUTH)
    assert res.status_code == 204 and res.content == b""

    for table, col in SCOPED.items():
        assert count(stack, table, col, "scope_a") == 0, table
    with stack.store._conn() as conn:
        links = conn.execute("SELECT * FROM source_links").fetchall()
        orphans = conn.execute(
            "SELECT COUNT(*) FROM source_links WHERE analysis_id NOT IN"
            " (SELECT analysis_id FROM analyses)"
        ).fetchone()[0]
    assert orphans == 0
    assert sorted((r["source_kind"], r["scope_key"]) for r in links) == [
        ("url", "scope_b"),
        ("youtube", ""),
    ]
    assert len(list(stack.settings.uploads_dir.iterdir())) == 1  # only scope_b's pending upload
    # nothing of scope_a is reachable, scope_b and the global entry are untouched
    assert stack.lookup("scope_a", sha256=sha(data)).status_code == 404
    assert stack.lookup("scope_a", source=f"url:{URL}").status_code == 404
    assert stack.lookup("scope_b", sha256=sha(keep)).status_code == 200
    assert stack.lookup("scope_b", source=f"url:{URL}").status_code == 200
    assert stack.get_analysis("an_global", "scope_a").status_code == 200
    assert stack.lookup(None, source="youtube:dQw4w9WgXcQ").status_code == 200
    # scope_a starts over: the same bytes are analysed again
    before = len(stack.analysed)
    assert stack.analyse_bytes(data, "scope_a").status_code == 202
    assert len(stack.analysed) == before + 1


def test_deleting_is_idempotent_and_validates_the_name(stack: Stack) -> None:
    for _ in range(2):
        assert stack.client.delete("/v2/scopes/never_used", headers=AUTH).status_code == 204
    bad = stack.client.delete("/v2/scopes/bad%20scope", headers=AUTH)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
    assert stack.client.delete("/v2/scopes/x").status_code == 401


def test_a_job_running_during_the_deletion_stores_nothing(stack: Stack) -> None:
    up = stack.upload(stack.audio("a.wav")).json()["upload_id"]

    def delete_while_running(*args, **kwargs):
        stack.client.delete(f"/v2/scopes/{SCOPE}", headers=AUTH)
        return stack._analyser(*args, **kwargs)

    stack.dispatch.then = lambda job_id: __import__(
        "app.service.worker", fromlist=["execute_job"]
    ).execute_job(
        job_id, stack.settings, stack.store, fetcher=stack._fetcher, analyser=delete_while_running
    )
    assert stack.analyse(up).status_code == 202
    for table in ("analyses", "jobs", "uploads", "source_links"):
        with stack.store._conn() as conn:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert audio_files(stack.settings) == []


def test_the_sqlite_file_holds_no_other_scope_rows_after_deleting_all(stack: Stack) -> None:
    fill(stack, "scope_a")
    fill(stack, "scope_b")
    for scope in ("scope_a", "scope_b"):
        assert stack.client.delete(f"/v2/scopes/{scope}", headers=AUTH).status_code == 204
    with sqlite3.connect(stack.settings.service_db_path) as conn:
        for table in ("analyses", "jobs", "uploads", "source_links", "alignments", "idempotency"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
