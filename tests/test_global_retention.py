"""Retention of the global analyses (ADR 0040 §7): unused for 90 days, a new analyzer version, and
the operator takedown of one source ref. Time is an injected clock; nothing sleeps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from app.service import retention_policy, sweep, takedown
from app.service.analysis_doc import ANALYZER_VERSION_V2
from app.service.cache_store import insert_analysis
from app.service.store import Store

from .service_helpers import service_settings

DAY = 86400.0
TTL = retention_policy.ANALYSIS_UNUSED_TTL_S
MARKER = "retention-marker-7f3a91"


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, days: float) -> None:
        self.now += days * DAY


def doc(ref: str, version: str = ANALYZER_VERSION_V2, kind: str = "youtube") -> Dict[str, Any]:
    source: Dict[str, Any] = {"kind": kind, "ref": ref, "match": {"title": MARKER + ref}}
    return {
        "provenance": {
            "audio": {"sha256": "sha-" + ref, "fingerprint": None},
            "analyzer": {"analyzer_version": version},
            "source": source,
        }
    }


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(tmp_path: Path, clock: Clock) -> Store:
    return Store(tmp_path / "data" / "service.db", clock=clock)


def put(store: Store, clock: Clock, ref: str, **kw: Any) -> str:
    """A global analysis of ``ref`` with its link, a global alignment and a match record."""
    analysis_id = "an_" + ref
    with store._conn() as conn:
        insert_analysis(conn, analysis_id, None, doc(ref, **kw), clock())
        conn.execute(
            "INSERT INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
            " VALUES ('', ?, ?, ?, ?)",
            (
                "k" + ref,
                json.dumps({"analysis_id": "al_" + ref, "base_analysis_id": analysis_id}),
                clock(),
                "al_" + ref,
            ),
        )
        conn.execute(
            "INSERT INTO match_choices (video_id, record, duration_s, created_at)"
            " VALUES (?, '{}', 1.0, ?)",
            (ref, clock()),
        )
        conn.execute(
            "INSERT INTO match_cache (fingerprint, entries, expires_at) VALUES (?, ?, ?)",
            ("fp" + ref, json.dumps([{"id": ref}]), clock() + DAY),
        )
    return analysis_id


def purge(store: Store, clock: Clock) -> Dict[str, int]:
    return store.purge_unused_analyses(clock(), ANALYZER_VERSION_V2, TTL)


def present(store: Store, ref: str) -> Dict[str, int]:
    """How many rows of each kind are held for ``ref``."""
    queries = {
        "analyses": ("SELECT COUNT(*) FROM analyses WHERE analysis_id = ?", "an_" + ref),
        "links": ("SELECT COUNT(*) FROM source_links WHERE source_ref = ?", ref),
        "alignments": ("SELECT COUNT(*) FROM alignments WHERE align_key = ?", "k" + ref),
        "choices": ("SELECT COUNT(*) FROM match_choices WHERE video_id = ?", ref),
        "cache": ("SELECT COUNT(*) FROM match_cache WHERE fingerprint = ?", "fp" + ref),
    }
    with store._conn() as conn:
        return {k: conn.execute(q, (arg,)).fetchone()[0] for k, (q, arg) in queries.items()}


def all_there(store: Store, ref: str) -> bool:
    return all(n == 1 for n in present(store, ref).values())


SHORT_LIVED = ("choices", "cache")  # match records and search results: their own 3-day lifetime


def gone(store: Store, ref: str, *, takedown: bool = True) -> bool:
    """Nothing held for ``ref``; the short-lived match rows only after a takedown."""
    return all(n == 0 for k, n in present(store, ref).items() if takedown or k not in SHORT_LIVED)


def test_unused_for_91_days_is_purged_and_89_is_kept(store: Store, clock: Clock) -> None:
    put(store, clock, "idle91")
    put(store, clock, "idle89")
    clock.advance(89)
    assert purge(store, clock) == {"analyses": 0, "links": 0, "alignments": 0}
    assert all_there(store, "idle91") and all_there(store, "idle89")
    clock.advance(2)  # 91 days since creation
    counts = purge(store, clock)
    assert counts == {"analyses": 2, "links": 2, "alignments": 2}


def test_a_read_at_day_89_keeps_the_entry_past_day_90(store: Store, clock: Clock) -> None:
    put(store, clock, "read89")
    put(store, clock, "never")
    clock.advance(89)
    assert store.find_by_source("youtube", "read89", None, ANALYZER_VERSION_V2) is not None
    clock.advance(2)  # day 91: 91 since creation, 2 since the read
    purge(store, clock)
    assert all_there(store, "read89")
    assert present(store, "never")["analyses"] == 0
    assert present(store, "never")["links"] == 0
    clock.advance(90)  # unread again for 90 days
    purge(store, clock)
    assert present(store, "read89")["analyses"] == 0


READS: List[Callable[[Store, str], Any]] = [
    lambda s, ref: s.find_by_source("youtube", ref, None, ANALYZER_VERSION_V2),
    lambda s, ref: s.find_global_by_sha("sha-" + ref, ANALYZER_VERSION_V2),
    lambda s, ref: s.get_analysis_in_scope("an_" + ref, "any_scope"),
    lambda s, ref: s.get_analysis_in_scope("an_" + ref, None),
]


@pytest.mark.parametrize("read", READS, ids=["source", "sha", "scoped_read", "global_read"])
def test_every_read_path_renews_an_entry(store: Store, clock: Clock, read: Callable) -> None:
    put(store, clock, "hot")
    clock.advance(60)
    assert read(store, "hot") is not None
    clock.advance(45)  # 105 days since creation, 45 since the read
    purge(store, clock)
    assert present(store, "hot")["analyses"] == 1


def test_reading_a_global_alignment_renews_its_analysis(store: Store, clock: Clock) -> None:
    from app.service.alignment_doc import AlignedRow

    put(store, clock, "al")
    row = AlignedRow("al_al", "an_al", {"offset_s": 1.0}, {})
    clock.advance(60)
    with store._conn() as conn:  # the alignment is fresh (its own 3-day lifetime), its analysis not
        conn.execute(
            "UPDATE alignments SET doc = ?, created_at = ? WHERE align_key = 'kal'",
            (json.dumps(row.as_doc()), clock()),
        )
    assert store.get_aligned_in_scope("al_al", None) is not None
    clock.advance(45)
    purge(store, clock)
    assert present(store, "al")["analyses"] == 1


def test_a_scoped_read_never_touches_a_scoped_entry_and_scoped_rows_are_never_purged(
    store: Store, clock: Clock
) -> None:
    with store._conn() as conn:
        insert_analysis(conn, "an_scoped", "scope_a", doc("s", kind="upload"), clock())
        conn.execute("UPDATE analyses SET analyzer_version = 'old' WHERE analysis_id='an_scoped'")
    clock.advance(400)
    assert purge(store, clock)["analyses"] == 0
    assert store.get_analysis_in_scope("an_scoped", "scope_a") is not None


def test_a_new_analyzer_version_purges_even_a_fresh_entry(store: Store, clock: Clock) -> None:
    put(store, clock, "old", version="1.0.0.old")
    put(store, clock, "cur")
    clock.advance(1)
    assert purge(store, clock)["analyses"] == 1
    assert gone(store, "old", takedown=False) and all_there(store, "cur")


def test_purge_is_idempotent(store: Store, clock: Clock) -> None:
    put(store, clock, "x")
    clock.advance(100)
    assert purge(store, clock)["analyses"] == 1
    assert purge(store, clock) == {"analyses": 0, "links": 0, "alignments": 0}


def test_a_dangling_global_link_is_purged(store: Store, clock: Clock) -> None:
    with store._conn() as conn:
        conn.execute("INSERT INTO source_links VALUES ('youtube', 'ghost', '', 'an_missing', 1.0)")
    assert purge(store, clock)["links"] == 1


def test_takedown_removes_the_ref_and_nothing_else(store: Store, clock: Clock) -> None:
    with store._conn() as conn:
        # an older analysis of the same ref, whose link the newer one below replaces
        insert_analysis(conn, "an_target2", None, doc("target"), clock())
        conn.execute(
            "UPDATE analyses SET sha256 = 'sha-other-bytes' WHERE analysis_id='an_target2'"
        )
    put(store, clock, "target")
    put(store, clock, "other")
    with store._conn() as conn:
        insert_analysis(conn, "an_sc", "scope_a", doc("sc", kind="upload"), clock())
    counts = store.takedown("youtube", "target")
    assert counts == {"analyses": 2, "links": 1, "alignments": 1, "match_records": 2}
    assert gone(store, "target")
    with store._conn() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM analyses WHERE analysis_id='an_target2'").fetchone()[
                0
            ]
            == 0
        )
    assert all_there(store, "other")
    assert store.get_analysis_in_scope("an_sc", "scope_a") is not None
    assert store.takedown("youtube", "target") == {
        "analyses": 0,
        "links": 0,
        "alignments": 0,
        "match_records": 0,
    }


def files_hold(root: Path, text: str) -> bool:
    return any(text.encode() in p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_nothing_of_a_purged_entry_is_left_on_disk(
    tmp_path: Path, store: Store, clock: Clock
) -> None:
    root = tmp_path / "data"
    put(store, clock, "gone1")
    put(store, clock, "gone2")
    put(store, clock, "kept")
    assert files_hold(root, MARKER + "gone1") and files_hold(root, MARKER + "gone2")
    store.takedown("youtube", "gone1")
    assert not files_hold(root, MARKER + "gone1")
    assert files_hold(root, MARKER + "kept")
    clock.advance(1)
    with store._conn() as conn:
        conn.execute("UPDATE analyses SET analyzer_version = 'old' WHERE analysis_id = 'an_gone2'")
    purge(store, clock)
    assert not files_hold(root, MARKER + "gone2")
    assert files_hold(root, MARKER + "kept")


def test_a_purge_leaves_nothing_in_the_log_of_a_database_another_connection_holds_open(
    tmp_path: Path, store: Store, clock: Clock
) -> None:
    import sqlite3

    keeper = sqlite3.connect(store.path)  # an open connection keeps the -wal file alive
    keeper.execute("SELECT COUNT(*) FROM analyses").fetchone()
    put(store, clock, "logged")
    wal = Path(str(store.path) + "-wal")
    assert MARKER.encode() + b"logged" in wal.read_bytes()
    store.takedown("youtube", "logged")
    assert MARKER.encode() + b"logged" not in wal.read_bytes()
    assert not files_hold(tmp_path / "data", MARKER + "logged")
    keeper.close()


def test_a_purge_overwrites_the_freed_pages(store: Store, clock: Clock) -> None:
    probe: List[int] = []

    class Spy(Store):
        def _end_purge(self, conn: Any, deleted: int) -> None:
            probe.append(conn.execute("PRAGMA secure_delete").fetchone()[0])
            super()._end_purge(conn, deleted)

    spy = Spy(store.path, clock=clock)
    put(spy, clock, "p")
    spy.takedown("youtube", "p")
    clock.advance(100)
    spy.purge_unused_analyses(clock(), ANALYZER_VERSION_V2, TTL)
    assert probe and set(probe) == {1}  # on, whatever the build default is


def test_a_takedown_never_deletes_a_scoped_analysis_a_link_points_to(
    store: Store, clock: Clock
) -> None:
    with store._conn() as conn:
        insert_analysis(conn, "an_sc", "scope_a", doc("sc", kind="upload"), clock())
        conn.execute("INSERT INTO source_links VALUES ('youtube', 'odd', '', 'an_sc', 1.0)")
    assert store.takedown("youtube", "odd")["analyses"] == 0
    assert store.get_analysis_in_scope("an_sc", "scope_a") is not None


def test_the_sweep_runs_the_purge_with_the_configured_period(tmp_path: Path, clock: Clock) -> None:
    settings = service_settings(tmp_path, global_analysis_ttl_s=2 * DAY)
    settings.ensure_dirs()
    store = Store(settings.service_db_path, clock=clock)
    put(store, clock, "swept")
    sweep.sweep(settings, store, clock.now + DAY)
    assert all_there(store, "swept")
    sweep.sweep(settings, store, clock.now + 3 * DAY)
    assert gone(store, "swept", takedown=False)


def test_the_period_defaults_to_90_days_and_has_a_floor(tmp_path: Path) -> None:
    from pydantic import ValidationError

    assert TTL == 90 * DAY
    assert service_settings(tmp_path).global_analysis_ttl_s == TTL
    with pytest.raises(ValidationError):
        service_settings(tmp_path, global_analysis_ttl_s=3600)


def test_an_older_database_counts_its_entries_from_creation(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE analyses (analysis_id TEXT PRIMARY KEY, cache_scope TEXT, doc TEXT NOT NULL,"
        " created_at REAL NOT NULL, sha256 TEXT, fingerprint TEXT, analyzer_version TEXT)"
    )
    conn.execute(
        "INSERT INTO analyses VALUES ('an_old', NULL, ?, 123.0, 's', NULL, ?)",
        (json.dumps(doc("o")), ANALYZER_VERSION_V2),
    )
    conn.commit()
    conn.close()
    store = Store(path)
    with store._conn() as c:
        assert c.execute("SELECT last_read_at FROM analyses").fetchone()[0] == 123.0


def test_the_takedown_command_reports_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    settings = service_settings(tmp_path, data_dir=tmp_path / "d")
    clock = Clock()
    store = Store(settings.service_db_path, clock=clock)
    put(store, clock, "dQw4w9WgXcQ")
    assert takedown.main(["youtube:dQw4w9WgXcQ"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "alignments=1 analyses=1 links=1 match_records=2"
    assert "dQw4w9WgXcQ" not in out and MARKER not in out
    assert gone(store, "dQw4w9WgXcQ")
    assert takedown.main(["youtube:dQw4w9WgXcQ"]) == 0
    assert capsys.readouterr().out.strip() == "alignments=0 analyses=0 links=0 match_records=0"


@pytest.mark.parametrize("bad", ["url:sha256:abc", "nonsense", "youtube:", "spotify:abc"])
def test_the_takedown_command_refuses_anything_but_a_global_source(
    bad: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    assert takedown.main([bad]) == 2
    assert takedown.main([]) == 2


def test_the_takedown_is_not_reachable_over_http(tmp_path: Path) -> None:
    from .service_helpers import AUTH, RecordingDispatch, client_for

    settings = service_settings(tmp_path)
    settings.ensure_dirs()
    client = client_for(settings, RecordingDispatch())
    paths = {getattr(r, "path", "") for r in client.app.routes}  # type: ignore[attr-defined]
    assert not any("takedown" in p or "retention" in p or "purge" in p for p in paths)
    for method in ("post", "delete", "put"):
        res = getattr(client, method)("/v2/takedown", headers=AUTH)
        assert res.status_code in (404, 405)


def scoped_rows(store: Store) -> List[Any]:
    """Every row that belongs to a scope, in every table the retention code deletes from."""
    queries = [
        "SELECT * FROM analyses WHERE cache_scope IS NOT NULL",
        "SELECT * FROM source_links WHERE scope_key != ''",
        "SELECT * FROM alignments WHERE cache_scope != ''",
    ]
    with store._conn() as conn:
        return [tuple(r) for q in queries for r in conn.execute(q).fetchall()]


def put_scoped(store: Store, clock: Clock, ref: str = "mine") -> None:
    """A scoped `url` analysis of an old analyzer version with its scoped link and alignment,
    and one of each that is also scoped but named like a global source."""
    with store._conn() as conn:
        insert_analysis(conn, "an_url", "scope_a", doc(ref, "old", kind="url"), clock())
        insert_analysis(conn, "an_yt", "scope_b", doc("scopedyt", "old", kind="youtube"), clock())
        # a scoped link that names a global source, and one whose analysis is gone
        conn.execute(
            "INSERT INTO source_links VALUES ('youtube', 'target', 'scope_c', 'an_yt', 1.0)"
        )
        conn.execute("INSERT INTO source_links VALUES ('url', 'dang', 'scope_a', 'an_none', 1.0)")
        for scope, base, key in (("scope_a", "an_url", "ku"), ("scope_b", "an_yt", "ky")):
            conn.execute(
                "INSERT INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    scope,
                    key,
                    json.dumps({"analysis_id": "al_" + key, "base_analysis_id": base}),
                    1.0,
                    "al_" + key,
                ),
            )


def test_a_purge_and_a_takedown_leave_every_scoped_row(store: Store, clock: Clock) -> None:
    put_scoped(store, clock)
    put(store, clock, "target")  # a global entry the takedown does remove
    before = scoped_rows(store)
    assert len(before) == 7  # 2 analyses, 3 links, 2 alignments
    clock.advance(400)  # far past the period, and at an old analyzer version
    assert purge(store, clock)["analyses"] == 1  # only the global one
    assert scoped_rows(store) == before
    assert store.takedown("youtube", "target")["analyses"] == 0
    assert scoped_rows(store) == before


def test_the_victims_of_a_purge_are_global_only(store: Store, clock: Clock) -> None:
    from app.service.retention_store import _unused_global_ids

    put_scoped(store, clock)
    put(store, clock, "g")
    clock.advance(400)
    with store._conn() as conn:
        assert _unused_global_ids(conn, clock(), TTL, ANALYZER_VERSION_V2) == ["an_g"]


def test_deleting_by_id_never_reaches_a_scoped_analysis_link_or_alignment(
    store: Store, clock: Clock
) -> None:
    from app.service.retention_store import _delete_entries

    put_scoped(store, clock)
    with store._conn() as conn:
        # a global-keyed link and a scoped link both pointing at the scoped analyses
        conn.execute("INSERT INTO source_links VALUES ('url', 'x', 'scope_a', 'an_url', 1.0)")
        before = scoped_rows(store)
        counts = _delete_entries(conn, ["an_url", "an_yt"])
    assert counts["analyses"] == 0 and counts["alignments"] == 0
    assert scoped_rows(store) == before and len(before) == 8


class Tracing(Store):
    """A store that records the SQL of every connection."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        self.sql: List[str] = []
        super().__init__(*a, **kw)

    def _conn(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def traced() -> Any:
            with super(Tracing, self)._conn() as conn:
                conn.set_trace_callback(self.sql.append)
                yield conn

        return traced()


def test_the_log_is_truncated_only_when_something_was_deleted(tmp_path: Path, clock: Clock) -> None:
    store = Tracing(tmp_path / "t.db", clock=clock)
    assert purge(store, clock) == {"analyses": 0, "links": 0, "alignments": 0}
    store.takedown("youtube", "nothing")
    assert not any("wal_checkpoint" in q for q in store.sql)
    put(store, clock, "t")
    store.takedown("youtube", "t")
    assert any("wal_checkpoint" in q for q in store.sql)


def test_a_busy_log_is_logged_not_raised_and_not_waited_for(
    store: Store, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    import sqlite3
    import time

    put(store, clock, "b")
    reader = sqlite3.connect(store.path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM analyses").fetchone()  # a read transaction on the log
    started = time.monotonic()
    with caplog.at_level("WARNING", logger="spotdown.service.retention"):
        assert store.takedown("youtube", "b")["analyses"] == 1
    assert time.monotonic() - started < 5  # the connection's wait is 30 s
    assert "could not be truncated" in caplog.text
    reader.close()
    assert gone(store, "b")


def test_a_touch_inside_the_interval_writes_nothing(store: Store, clock: Clock) -> None:
    put(store, clock, "w")
    clock.advance(1 / 24 / 2)  # half an hour: inside the interval
    store.find_by_source("youtube", "w", None, ANALYZER_VERSION_V2)
    with store._conn() as conn:
        first = conn.execute("SELECT last_read_at FROM analyses").fetchone()[0]
    assert first == 1_000_000.0
    clock.advance(1 / 24)
    store.find_by_source("youtube", "w", None, ANALYZER_VERSION_V2)
    with store._conn() as conn:
        assert conn.execute("SELECT last_read_at FROM analyses").fetchone()[0] == clock()
