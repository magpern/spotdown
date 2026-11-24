"""Retention of scoped entries (spotdown#40): a scope that is never deleted keeps nothing for ever.
A scoped analysis unread for the period or of an old analyzer version goes with its own scope's
links and alignments; its old job and idempotency records go; a running job's rows never do; no
global row is ever selected. Time is an injected clock; nothing sleeps, nothing leaves the host."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from app.config import Settings
from app.service import retention_policy, sweep
from app.service.analysis_doc import ANALYZER_VERSION_V2
from app.service.cache_store import insert_analysis
from app.service.store import Store
from app.service.uploads import upload_path
from app.service.worker import execute_job

from .service_helpers import SCOPE, fake_analyser_result, service_settings, write_wav
from .test_global_retention import DAY, MARKER, Clock, doc, files_hold, present, put

TTL = retention_policy.ANALYSIS_UNUSED_TTL_S
EMPTY = {"analyses": 0, "links": 0, "alignments": 0, "jobs": 0, "idempotency": 0}


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(tmp_path: Path, clock: Clock) -> Store:
    return Store(tmp_path / "data" / "service.db", clock=clock)


def put_mine(
    store: Store, clock: Clock, ref: str, scope: str = SCOPE, version: str = ANALYZER_VERSION_V2
) -> str:
    """A scoped `url` analysis of ``ref`` with its scoped link and a scoped alignment on it."""
    analysis_id = "an_" + ref
    with store._conn() as conn:
        insert_analysis(conn, analysis_id, scope, doc(ref, version, kind="url"), clock())
        conn.execute(
            "INSERT INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                scope,
                "k" + ref,
                json.dumps({"analysis_id": "al_" + ref, "base_analysis_id": analysis_id}),
                clock(),
                "al_" + ref,
            ),
        )
    return analysis_id


def held(store: Store, ref: str) -> Dict[str, int]:
    """Rows held for the scoped ``ref``: its analysis, link and alignment."""
    queries = {
        "analyses": ("SELECT COUNT(*) FROM analyses WHERE analysis_id = ?", "an_" + ref),
        "links": ("SELECT COUNT(*) FROM source_links WHERE source_ref = ?", ref),
        "alignments": ("SELECT COUNT(*) FROM alignments WHERE align_key = ?", "k" + ref),
    }
    with store._conn() as conn:
        return {k: conn.execute(q, (arg,)).fetchone()[0] for k, (q, arg) in queries.items()}


def kept(store: Store, ref: str) -> bool:
    return held(store, ref) == {"analyses": 1, "links": 1, "alignments": 1}


def purged(store: Store, ref: str) -> bool:
    return held(store, ref) == {"analyses": 0, "links": 0, "alignments": 0}


def purge(store: Store, clock: Clock) -> Dict[str, int]:
    return store.purge_scoped(clock(), ANALYZER_VERSION_V2, TTL)


def global_rows(store: Store) -> List[Any]:
    """Every global row the retention code could delete from."""
    queries = [
        "SELECT * FROM analyses WHERE cache_scope IS NULL",
        "SELECT * FROM source_links WHERE scope_key = ''",
        "SELECT * FROM alignments WHERE cache_scope = ''",
        "SELECT * FROM jobs WHERE cache_scope IS NULL",
        "SELECT * FROM match_choices",
        "SELECT * FROM match_cache",
    ]
    with store._conn() as conn:
        return [tuple(r) for q in queries for r in conn.execute(q).fetchall()]


def add_job(store: Store, job_id: str, scope: Optional[str], status: str, at: float) -> None:
    store.add_job({"job_id": job_id, "cache_scope": scope, "source_kind": "url"})
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?", (status, at, job_id)
        )


def job_ids(store: Store) -> List[str]:
    with store._conn() as conn:
        return sorted(r[0] for r in conn.execute("SELECT job_id FROM jobs"))


# ---- analyses ----


def test_a_scoped_analysis_unused_for_91_days_is_purged_and_89_is_kept(
    store: Store, clock: Clock
) -> None:
    put_mine(store, clock, "old")
    clock.advance(2)
    put_mine(store, clock, "young")
    clock.advance(89)  # "old" is 91 days unread, "young" 89
    counts = purge(store, clock)
    assert counts["analyses"] == 1 and counts["links"] == 1 and counts["alignments"] == 1
    assert purged(store, "old") and kept(store, "young")


def test_exactly_the_period_is_purged(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "edge")
    clock.advance(90)
    purge(store, clock)
    assert purged(store, "edge")


def _sha_hit(store: Store) -> Any:
    return store.find_by_sha(SCOPE, "sha-r", ANALYZER_VERSION_V2)


def _source_hit(store: Store) -> Any:
    return store.find_by_source("url", "r", SCOPE, ANALYZER_VERSION_V2)


def _id_read(store: Store) -> Any:
    return store.get_analysis_in_scope("an_r", SCOPE)


@pytest.mark.parametrize("read", [_sha_hit, _source_hit, _id_read])
def test_every_scoped_read_path_renews_the_entry(
    store: Store, clock: Clock, read: Callable
) -> None:
    put_mine(store, clock, "r")
    clock.advance(89)
    assert read(store) is not None
    clock.advance(89)  # 178 days since it was stored, 89 since the read
    purge(store, clock)
    assert kept(store, "r")
    clock.advance(2)
    purge(store, clock)
    assert purged(store, "r")


def test_reading_a_scoped_alignment_renews_its_analysis(store: Store, clock: Clock) -> None:
    from app.service.alignment_doc import AlignedRow

    put_mine(store, clock, "al")
    row = AlignedRow("al_al", "an_al", {"offset_s": 1.0}, {})
    with store._conn() as conn:
        conn.execute(
            "UPDATE alignments SET doc = ? WHERE align_key = 'kal'", (json.dumps(row.as_doc()),)
        )
    clock.advance(89)
    assert store.get_aligned_in_scope("al_al", SCOPE) is not None
    clock.advance(45)
    purge(store, clock)
    assert held(store, "al")["analyses"] == 1


def test_a_read_from_another_scope_renews_nothing(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "r")
    clock.advance(89)
    assert store.get_analysis_in_scope("an_r", "scope_other") is None
    assert store.find_by_sha("scope_other", "sha-r", ANALYZER_VERSION_V2) is None
    clock.advance(2)
    purge(store, clock)
    assert purged(store, "r")


def test_an_old_analyzer_version_is_purged_even_when_fresh_and_the_current_one_is_kept(
    store: Store, clock: Clock
) -> None:
    put_mine(store, clock, "stale", version="1.0.0.old")
    put_mine(store, clock, "current")
    clock.advance(1 / 24)
    assert purge(store, clock)["analyses"] == 1
    assert purged(store, "stale") and kept(store, "current")


def test_an_analysis_with_no_recorded_version_is_purged(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "nover")
    with store._conn() as conn:
        conn.execute("UPDATE analyses SET analyzer_version = NULL")
    assert purge(store, clock)["analyses"] == 1


def test_the_purge_is_idempotent(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "x")
    clock.advance(100)
    assert purge(store, clock)["analyses"] == 1
    assert purge(store, clock) == EMPTY


def test_each_scope_keeps_its_own_entries(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "a1", scope="scope_a")
    put_mine(store, clock, "b1", scope="scope_b", version="old")
    clock.advance(1)
    purge(store, clock)
    assert kept(store, "a1") and purged(store, "b1")


# ---- global rows ----


def test_no_global_row_is_ever_selected(store: Store, clock: Clock) -> None:
    put(store, clock, "g_old", version="1.0.0.old")  # stale version: the global purge's to take
    put(store, clock, "g")
    add_job(store, "job_global", None, "done", clock())
    put_mine(store, clock, "mine", version="old")
    before = global_rows(store)
    assert len(before) == 11  # 2 analyses, links, alignments, match choices and caches; a job
    clock.advance(400)  # far past every period, at an old analyzer version
    counts = purge(store, clock)
    assert counts["analyses"] == 1  # the scoped one only
    assert global_rows(store) == before


def test_the_victims_are_scoped_only(store: Store, clock: Clock) -> None:
    from app.service.scope_retention_store import _unused_scoped_ids

    put(store, clock, "g", version="old")
    put_mine(store, clock, "mine")
    clock.advance(400)
    with store._conn() as conn:
        assert _unused_scoped_ids(conn, clock(), TTL, ANALYZER_VERSION_V2) == [("an_mine", SCOPE)]


def test_deleting_by_id_reaches_only_rows_of_the_given_scope(store: Store, clock: Clock) -> None:
    from app.service.scope_retention_store import _delete_scoped_entries

    put(store, clock, "g")
    put_mine(store, clock, "mine")
    before = global_rows(store)
    with store._conn() as conn:
        counts = _delete_scoped_entries(
            conn, [("an_g", ""), ("an_g", None), ("an_g", SCOPE), ("an_mine", "scope_other")]
        )
    assert counts == {"analyses": 0, "links": 0, "alignments": 0}
    assert global_rows(store) == before and kept(store, "mine")


def test_the_global_purge_still_never_selects_a_scoped_entry(store: Store, clock: Clock) -> None:
    put_mine(store, clock, "mine", version="old")
    clock.advance(400)
    assert store.purge_unused_analyses(clock(), ANALYZER_VERSION_V2, TTL)["analyses"] == 0
    assert kept(store, "mine")


def test_dangling_scoped_links_and_alignments_go_and_global_ones_stay(
    store: Store, clock: Clock
) -> None:
    put(store, clock, "g")
    with store._conn() as conn:
        conn.execute("INSERT INTO source_links VALUES ('url', 'dang', 'scope_a', 'an_none', 1.0)")
        conn.execute(
            "INSERT INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
            " VALUES ('scope_a', 'kd', ?, 1.0, 'al_d')",
            (json.dumps({"analysis_id": "al_d", "base_analysis_id": "an_none"}),),
        )
    before = global_rows(store)
    counts = purge(store, clock)
    assert counts["links"] == 1 and counts["alignments"] == 1
    assert global_rows(store) == before


# ---- jobs and idempotency records ----


def test_old_scoped_job_records_go_and_running_fresh_and_global_ones_stay(
    store: Store, clock: Clock
) -> None:
    old = clock() - 91 * DAY
    for status in ("done", "failed", "queued"):
        add_job(store, "job_old_" + status, SCOPE, status, old)
    add_job(store, "job_old_running", SCOPE, "running", old)
    add_job(store, "job_young", SCOPE, "done", clock() - 89 * DAY)
    add_job(store, "job_old_global", None, "done", old)
    assert purge(store, clock)["jobs"] == 3
    assert job_ids(store) == ["job_old_global", "job_old_running", "job_young"]


def test_idempotency_records_past_their_window_go(store: Store, clock: Clock) -> None:
    from app.service.idempotency_policy import WINDOW_S

    with store._conn() as conn:
        for key, age in (("old", WINDOW_S + 1), ("young", WINDOW_S - 60)):
            conn.execute(
                "INSERT INTO idempotency VALUES (?, ?, 'h', 200, '{}', ?)",
                (SCOPE, key, clock() - age),
            )
    assert purge(store, clock)["idempotency"] == 1
    with store._conn() as conn:
        assert [r[0] for r in conn.execute("SELECT idem_key FROM idempotency")] == ["young"]


# ---- nothing left behind ----


def test_nothing_of_a_purged_scoped_entry_is_left_on_disk(
    tmp_path: Path, store: Store, clock: Clock
) -> None:
    root = tmp_path / "data"
    put_mine(store, clock, "gone")
    put_mine(store, clock, "stays")
    with store._conn() as conn:
        conn.execute("UPDATE analyses SET analyzer_version = 'old' WHERE analysis_id = 'an_gone'")
    assert files_hold(root, MARKER + "gone")
    purge(store, clock)
    assert not files_hold(root, MARKER + "gone") and files_hold(root, MARKER + "stays")


def test_the_scoped_purge_overwrites_the_freed_pages(store: Store, clock: Clock) -> None:
    probe: List[int] = []

    class Spy(Store):
        def _end_purge(self, conn: Any, deleted: int) -> None:
            probe.append(conn.execute("PRAGMA secure_delete").fetchone()[0])
            super()._end_purge(conn, deleted)

    spy = Spy(store.path, clock=clock)
    put_mine(spy, clock, "p", version="old")
    purge(spy, clock)
    assert probe == [1]


# ---- the sweep and its setting ----


def sweep_settings(tmp_path: Path, **kw: Any) -> Settings:
    settings = service_settings(tmp_path, **kw)
    settings.ensure_dirs()
    return settings


def test_the_sweep_runs_the_scoped_purge_with_its_own_period(tmp_path: Path, clock: Clock) -> None:
    settings = sweep_settings(tmp_path, scoped_retention_ttl_s=2 * DAY)
    store = Store(settings.service_db_path, clock=clock)
    put_mine(store, clock, "swept")
    put(store, clock, "g")  # global: 90 days, untouched by the scoped period
    sweep.sweep(settings, store, clock.now + DAY)
    assert kept(store, "swept")
    sweep.sweep(settings, store, clock.now + 3 * DAY)
    assert purged(store, "swept") and present(store, "g")["analyses"] == 1


def test_the_period_defaults_to_90_days_and_has_a_floor(tmp_path: Path) -> None:
    from pydantic import ValidationError

    assert service_settings(tmp_path).scoped_retention_ttl_s == 90 * DAY
    with pytest.raises(ValidationError):
        service_settings(tmp_path, scoped_retention_ttl_s=3600)


def _upload(settings: Settings, store: Store, upload_id: str, expired_s: float) -> None:
    upload_path(settings, upload_id).write_bytes(b"RIFF audio")
    store.add_upload(upload_id, SCOPE, "a" * 64, 10, "wav", 3600)
    with store._conn() as conn:
        conn.execute(
            "UPDATE uploads SET expires_at = ? WHERE upload_id = ?",
            (time.time() - expired_s, upload_id),
        )


def test_the_sweep_removes_expired_uploads_without_a_new_upload(tmp_path: Path) -> None:
    settings = sweep_settings(tmp_path)
    store = Store(settings.service_db_path)
    _upload(settings, store, "up_unused", 1)
    _upload(settings, store, "up_live", -600)  # not yet expired
    sweep.sweep(settings, store)
    assert store.get_upload("up_unused") is None
    assert not upload_path(settings, "up_unused").exists()
    assert store.get_upload("up_live") is not None and upload_path(settings, "up_live").exists()


def test_the_sweep_never_removes_the_upload_of_a_running_job(tmp_path: Path) -> None:
    settings = sweep_settings(tmp_path)
    store = Store(settings.service_db_path)
    _upload(settings, store, "up_busy", 100_000)  # far past its TTL and the lost-job grace
    assert store.claim_upload("up_busy", SCOPE) is None  # expired, so mark it used by hand
    with store._conn() as conn:
        conn.execute("UPDATE uploads SET consumed = 1")
    store.add_job(
        {"job_id": "job_busy", "cache_scope": SCOPE, "source_kind": "upload",
         "upload_id": "up_busy"}
    )  # fmt: skip
    assert store.start_job("job_busy")
    sweep.sweep(settings, store)  # the job is fresh: inside its lease
    assert store.get_upload("up_busy") is not None and upload_path(settings, "up_busy").exists()
    store.finish_job_failed("job_busy", "processing_failed", None, None)
    sweep.sweep(settings, store)
    assert store.get_upload("up_busy") is None and not upload_path(settings, "up_busy").exists()


# ---- a running job's analysis ----


def _upload_job(settings: Settings, store: Store, audio: Path, job_id: str) -> None:
    upload_id = "up_" + job_id
    data = audio.read_bytes()
    upload_path(settings, upload_id).write_bytes(data)
    store.add_upload(upload_id, SCOPE, "a" * 64, len(data), "wav", 3600)
    assert store.claim_upload(upload_id, SCOPE) is not None
    store.add_job(
        {"job_id": job_id, "cache_scope": SCOPE, "source_kind": "upload", "upload_id": upload_id}
    )


def _fake_ok(audio: Path, media: str, work: Path, limits: Any) -> Dict[str, Any]:
    return fake_analyser_result()


def test_a_job_that_reuses_an_analysis_purged_meanwhile_is_never_done_on_it(
    tmp_path: Path,
) -> None:
    """Another worker (of a newer analyzer version) purges the analysis after this job read it
    and before the job ends on it: the job fails (the caller may retry), never done on a missing
    id."""
    settings = sweep_settings(tmp_path)

    class Racing(Store):
        def finish_job_reused(self, *args: Any, **kwargs: Any) -> bool:
            self.purge_scoped(time.time(), "9.9.9.newer", TTL)
            return super().finish_job_reused(*args, **kwargs)

    plain = Store(settings.service_db_path)
    _upload_job(settings, plain, write_wav(tmp_path / "a.wav", 2), "job_first")
    execute_job("job_first", settings, plain, analyser=_fake_ok)
    first = plain.get_job("job_first")
    assert first is not None and first["status"] == "done"

    racing = Racing(settings.service_db_path)
    _upload_job(settings, racing, write_wav(tmp_path / "b.wav", 2), "job_second")
    execute_job("job_second", settings, racing, analyser=_fake_ok)
    second = racing.get_job("job_second")
    assert second is not None
    assert second["status"] == "failed" and second["outcome"] == "source_unavailable"
    assert second["analysis_id"] is None
    assert racing.get_analysis(first["analysis_id"]) is None


def test_a_job_that_reuses_an_analysis_renews_it(tmp_path: Path) -> None:
    settings = sweep_settings(tmp_path)
    store = Store(settings.service_db_path)
    _upload_job(settings, store, write_wav(tmp_path / "a.wav", 2), "job_first")
    execute_job("job_first", settings, store, analyser=_fake_ok)
    first = store.get_job("job_first")
    assert first is not None
    with store._conn() as conn:  # last read 89 days ago
        conn.execute("UPDATE analyses SET last_read_at = ?", (time.time() - 89 * DAY,))
    _upload_job(settings, store, write_wav(tmp_path / "b.wav", 2), "job_second")
    execute_job("job_second", settings, store, analyser=_fake_ok)
    second = store.get_job("job_second")
    assert second is not None and second["analysis_id"] == first["analysis_id"]
    store.purge_scoped(time.time() + 2 * DAY, ANALYZER_VERSION_V2, TTL)
    assert store.get_analysis(first["analysis_id"]) is not None


def test_a_failing_scoped_purge_neither_blocks_the_job_nor_keeps_expired_audio(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = sweep_settings(tmp_path)

    class Broken(Store):
        def purge_scoped(self, *args: Any, **kwargs: Any) -> Dict[str, int]:
            raise RuntimeError("disk I/O error")

    store = Broken(settings.service_db_path)
    _upload(settings, store, "up_expired", 1)
    _upload_job(settings, store, write_wav(tmp_path / "a.wav", 2), "job_one")
    with caplog.at_level("ERROR", logger="spotdown.service.sweep"):
        execute_job("job_one", settings, store, analyser=_fake_ok)
    assert "sweep step purge_scoped failed: RuntimeError" in caplog.text
    job = store.get_job("job_one")
    assert job is not None and job["status"] == "done"
    assert store.get_upload("up_expired") is None
    assert not upload_path(settings, "up_expired").exists()
