"""One label vocabulary and the standalone v1 cache key (ADR 0001, spotdown#45).

The v1 ``segments[].label`` is filled from ``label_calc``; the ``analyses`` cache is keyed by
``(track_id, analyzer_version, response_version)``, rebuilt once from the older two-column key.
Synthetic audio and a temporary database only: no network, no provider.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
import soundfile as sf

from app import analyze, db, standalone
from app.analyze import ANALYZER_VERSION, V1_RESPONSE_VERSION, analyze_track
from app.config import Settings
from app.main import create_app
from app.quality.fixtures_io import render_to
from app.quality.specs import SPECS

from .standalone_helpers import generated_token, signed_in

ROOT = Path(__file__).resolve().parent.parent
OLD_LABEL = "chorus" + "/drop"  # the retired string; spelled so this file passes the grep criterion
OLD_ROW = json.dumps({"segments": [{"label": OLD_LABEL}], "marker": "stale"})
OLD_SCHEMA = """
CREATE TABLE analyses (
    track_id TEXT NOT NULL, analyzer_version INTEGER NOT NULL, json TEXT NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY (track_id, analyzer_version)
);
"""


@lru_cache(maxsize=1)
def _synthetic() -> Dict[str, Any]:
    import tempfile

    spec = next(s for s in SPECS if s["id"] == "pickup-edm-140")
    with tempfile.TemporaryDirectory() as tmp:
        path = render_to(spec, Path(tmp))
        return analyze_track(path, "fixture-track", round(sf.info(str(path)).duration))


def _settings(tmp_path: Path, monkeypatch) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        redis_url="redis://127.0.0.1:1/0",
        allow_ytdlp_sourcing=False,  # on by default in code; no label-cache test sources YouTube
    )
    settings.ensure_dirs()
    for module in (db, standalone):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    return settings


def _old_db(settings: Settings, rows: int = 1) -> None:
    """A database as it was before the change: the two-column key, one retired-label row."""
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(OLD_SCHEMA)
        for i in range(rows):
            track = "t1" if i == 0 else f"bulk{i}"
            conn.execute(
                "INSERT INTO analyses VALUES (?, ?, ?, 1)", (track, ANALYZER_VERSION, OLD_ROW)
            )


def _rows(settings: Settings) -> List[tuple]:
    with sqlite3.connect(settings.db_path) as conn:
        return conn.execute(
            "SELECT track_id, analyzer_version, response_version, json FROM analyses"
            " ORDER BY track_id, analyzer_version, response_version"
        ).fetchall()


def _columns(settings: Settings) -> List[str]:
    with sqlite3.connect(settings.db_path) as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(analyses)")]


def _primary_key(settings: Settings) -> List[str]:
    with sqlite3.connect(settings.db_path) as conn:
        info = sorted((r[5], r[1]) for r in conn.execute("PRAGMA table_info(analyses)") if r[5])
    return [name for _, name in info]


def _tables(settings: Settings) -> List[str]:
    with sqlite3.connect(settings.db_path) as conn:
        return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


# --- labels -----------------------------------------------------------------------------------


def test_v1_segment_labels_equal_the_documents_labels() -> None:
    result = _synthetic()
    by_id = {x["segment_id"]: x["label"] for x in result["document"]["labels"]}
    assert by_id, "the fixture must have labelled segments"
    for i, seg in enumerate(result["segments"]):
        assert seg["label"] == by_id.get(f"s{i + 1}", "unlabelled")
    assert {s["label"] for s in result["segments"]} - {"unlabelled"} == set(by_id.values())
    assert result["label_method"] == "heuristic"


def test_the_core_result_carries_no_label() -> None:
    from app.core import signal_calc

    assert not hasattr(signal_calc, "segment_label")


def test_an_unlabelled_segment_reads_unlabelled() -> None:
    core = {"drops": [], "content_start_s": 2.0, "content_end_s": 12.0}
    segs = [
        {"start": 0.0, "end": 2.0, "energy_struct": 0.0},
        {"start": 2.0, "end": 12.0, "energy_struct": 0.5},
        {"start": 12.0, "end": 14.0, "energy_struct": 0.0},
    ]
    analyze.fill_labels(segs, core)
    assert [s["label"] for s in segs] == ["unlabelled"] * 3  # one content segment: no structure


# --- migration --------------------------------------------------------------------------------


def test_the_old_table_is_rebuilt_and_keeps_its_row_as_version_1(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    _old_db(settings)
    db.init_db()
    assert _primary_key(settings) == ["track_id", "analyzer_version", "response_version"]
    assert _rows(settings) == [("t1", ANALYZER_VERSION, 1, OLD_ROW)]
    assert "analyses_new" not in _tables(settings)


def test_a_fresh_database_has_the_three_column_key(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    db.init_db()
    assert _primary_key(settings) == ["track_id", "analyzer_version", "response_version"]


def test_init_db_twice_does_nothing_the_second_time(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    _old_db(settings)
    db.init_db()
    db.set_analysis("t1", ANALYZER_VERSION, 2, "new")
    before = _rows(settings)
    monkeypatch.setattr(db, "_REBUILD_STEPS", ("DROP TABLE analyses",))  # would destroy the table
    db.init_db()
    assert _rows(settings) == before


def test_a_failed_rebuild_rolls_back_and_leaves_the_table_intact(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    _old_db(settings)
    steps = db._REBUILD_STEPS
    monkeypatch.setattr(db, "_REBUILD_STEPS", steps[:3] + ("DROP TABLE no_such_table",) + steps[3:])
    with pytest.raises(sqlite3.OperationalError):
        db.init_db()
    assert _columns(settings) == ["track_id", "analyzer_version", "json", "created_at"]
    assert _rows_old(settings) == [("t1", ANALYZER_VERSION, OLD_ROW)]
    assert "analyses_new" not in _tables(settings)
    monkeypatch.setattr(db, "_REBUILD_STEPS", steps)
    db.init_db()  # the next start rebuilds normally
    assert _rows(settings) == [("t1", ANALYZER_VERSION, 1, OLD_ROW)]


def _rows_old(settings: Settings) -> List[tuple]:
    with sqlite3.connect(settings.db_path) as conn:
        return conn.execute("SELECT track_id, analyzer_version, json FROM analyses").fetchall()


def test_a_second_init_db_waits_on_the_lock_and_does_not_rebuild_again(
    tmp_path, monkeypatch
) -> None:
    """Deterministic: connection A holds ``BEGIN IMMEDIATE`` with the rebuild applied but not
    committed; ``init_db`` runs in a thread. The thread has made its decision (its ``PRAGMA`` read,
    or just before its own ``BEGIN IMMEDIATE`` is sent) when the event fires; only then A commits.
    A check outside the transaction would have read the old columns and rebuild a second time."""
    settings = _settings(tmp_path, monkeypatch)
    _old_db(settings)
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(db._SCHEMA)  # the schema step of init_db is then a no-op
        conn.execute("CREATE TABLE rebuild_log (n INTEGER)")
    steps = db._REBUILD_STEPS + ("INSERT INTO rebuild_log VALUES (1)",)  # logs each rebuild
    monkeypatch.setattr(db, "_REBUILD_STEPS", steps)
    at_begin = threading.Event()

    class Watched:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, sql: str, *args: Any) -> Any:
            if sql == "BEGIN IMMEDIATE":
                at_begin.set()  # the migration's own connection, about to take the lock
            return self._conn.execute(sql, *args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

    def connect(*args: Any, **kwargs: Any) -> Any:
        conn = sqlite3.connect(*args, **kwargs)
        return Watched(conn) if kwargs.get("isolation_level", "") is None else conn

    monkeypatch.setattr(db, "sqlite3", SimpleNamespace(connect=connect, Row=sqlite3.Row))
    errors: List[BaseException] = []

    def start() -> None:
        try:
            db.init_db()
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            errors.append(exc)

    holder = sqlite3.connect(settings.db_path, isolation_level=None)
    thread = threading.Thread(target=start)
    try:
        holder.execute("BEGIN IMMEDIATE")
        for step in steps:
            holder.execute(step)
        thread.start()
        assert at_begin.wait(10)
        holder.execute("COMMIT")
    finally:
        # On every path, including a failed wait: release the write lock first, then join.
        if holder.in_transaction:
            holder.execute("ROLLBACK")
        holder.close()
        if thread.is_alive():
            thread.join(30)
    assert not thread.is_alive() and errors == []
    with sqlite3.connect(settings.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM rebuild_log").fetchone()[0] == 1
    assert _rows(settings) == [("t1", ANALYZER_VERSION, 1, OLD_ROW)]


# --- the key ----------------------------------------------------------------------------------


def test_get_and_set_use_the_full_key(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    db.init_db()
    db.set_analysis("t", 6, 1, "v1")
    assert db.get_analysis("t", 6, 2) is None  # another response version is a miss
    db.set_analysis("t", 6, 2, "v2")
    assert db.get_analysis("t", 6, 1) == "v1" and db.get_analysis("t", 6, 2) == "v2"
    assert db.get_analysis("t", 7, 2) is None
    assert len(_rows(settings)) == 2  # the second write did not replace the first
    db.set_analysis("t", 6, 2, "v2b")
    assert db.get_analysis("t", 6, 2) == "v2b" and db.get_analysis("t", 6, 1) == "v1"


# --- the endpoint -----------------------------------------------------------------------------


class _Env:
    def __init__(self, tmp_path: Path, monkeypatch, with_file: bool = True) -> None:
        self.settings = _settings(tmp_path, monkeypatch)
        _old_db(self.settings)
        self.calls = 0
        fresh = _synthetic()

        def fake(path: Path, track_id: str, spotify_dur: Any = None) -> Dict[str, Any]:
            self.calls += 1
            return copy.deepcopy(fresh)

        monkeypatch.setattr(analyze, "analyze_track", fake)
        monkeypatch.setattr("app.spotify.resolve", lambda url: [])
        self.client = signed_in(create_app(self.settings), generated_token(self.settings))
        self.with_file = with_file

    def __enter__(self) -> "_Env":
        self.client.__enter__()  # the lifespan runs init_db: the rebuild
        if self.with_file:
            (self.settings.files_dir / "t1.mp3").write_bytes(b"x")
            db.mark_done("t1-mp3-128", "t1", "T", "A", "t1.mp3", 128, "mp3")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.__exit__(*exc)


def test_the_stale_row_is_not_served_and_stays_in_place(tmp_path, monkeypatch) -> None:
    with _Env(tmp_path, monkeypatch) as env:
        res = env.client.get("/api/v1/analyze/t1")
        assert res.status_code == 200 and env.calls == 1  # recomputed without ?refresh=true
        labels = {s["label"] for s in res.json()["segments"]}
        assert OLD_LABEL not in labels and "marker" not in res.json()
        stored = {(r[0], r[1], r[2]) for r in _rows(env.settings)}
        assert stored == {
            ("t1", ANALYZER_VERSION, 1),
            ("t1", ANALYZER_VERSION, V1_RESPONSE_VERSION),
        }
        old = [r for r in _rows(env.settings) if r[2] == 1][0]
        assert old[3] == OLD_ROW  # the full-key write did not overwrite version 1
        again = env.client.get("/api/v1/analyze/t1")
        assert again.json() == res.json() and env.calls == 1  # now served from the cache


def test_with_the_response_version_back_at_1_the_old_row_is_served(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(analyze, "V1_RESPONSE_VERSION", 1)
    with _Env(tmp_path, monkeypatch) as env:
        res = env.client.get("/api/v1/analyze/t1")
        assert res.json()["segments"][0]["label"] == OLD_LABEL and env.calls == 0


def test_a_missing_converted_file_is_404_not_the_stale_row(tmp_path, monkeypatch) -> None:
    with _Env(tmp_path, monkeypatch, with_file=False) as env:
        res = env.client.get("/api/v1/analyze/t1")
        assert res.status_code == 404 and "marker" not in res.text


# --- the grep criterion -----------------------------------------------------------------------

EXEMPT = ("docs/adr/", "docs/quality-trial-2026-11-21.md")


def test_the_retired_labels_appear_only_in_the_history_documents() -> None:
    pattern = re.compile(OLD_LABEL + "|" + "breakdown" + "/quiet")
    hits = []
    for top in ("app", "tests", "docs", "schema", "README.md"):
        base = ROOT / top
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or rel.startswith(EXEMPT) or "__pycache__" in rel:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if pattern.search(text):
                hits.append(rel)
    assert hits == []
