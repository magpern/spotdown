"""A malformed alignment document never makes a purge raise (#67 review): no purge parses an
alignment's document without ``json_valid`` first. A malformed scoped alignment can never be
served, so the scoped purge removes it; a global one is not the scoped purge's to touch."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service.analysis_doc import ANALYZER_VERSION_V2
from app.service.store import Store

from .service_helpers import SCOPE
from .test_global_retention import DAY, Clock, present, put
from .test_scoped_retention import TTL, kept, purged, put_mine

BAD = "{not json"


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(tmp_path: Path, clock: Clock) -> Store:
    return Store(tmp_path / "data" / "service.db", clock=clock)


def _bad_alignment(store: Store, clock: Clock, scope: str, key: str) -> None:
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO alignments (cache_scope, align_key, doc, created_at, analysis_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (scope, key, BAD, clock(), "al_" + key),
        )


def _alignment_keys(store: Store) -> list:
    with store._conn() as conn:
        return sorted(r[0] for r in conn.execute("SELECT align_key FROM alignments"))


def test_a_malformed_scoped_alignment_never_makes_the_scoped_purge_raise(
    store: Store, clock: Clock
) -> None:
    put_mine(store, clock, "old")
    _bad_alignment(store, clock, SCOPE, "bad_scoped")
    _bad_alignment(store, clock, "", "bad_global")
    clock.advance(TTL / DAY + 1)
    put_mine(store, clock, "fresh")
    counts = store.purge_scoped(clock.now, ANALYZER_VERSION_V2, TTL)  # must not raise
    assert purged(store, "old") and kept(store, "fresh")
    # the malformed scoped row is unservable and goes; the global one is not this purge's
    assert _alignment_keys(store) == ["bad_global", "kfresh"]
    assert counts["alignments"] == 2  # "old"'s alignment and the malformed scoped one


def test_a_malformed_alignment_never_makes_the_global_purge_raise(
    store: Store, clock: Clock
) -> None:
    put(store, clock, "g")
    _bad_alignment(store, clock, "", "bad_global")
    clock.advance(TTL / DAY + 1)
    store.purge_unused_analyses(clock.now, ANALYZER_VERSION_V2, TTL)  # must not raise
    assert present(store, "g")["analyses"] == 0 and present(store, "g")["alignments"] == 0
