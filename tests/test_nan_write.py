"""A NaN or Infinity never reaches a stored document (#72 review): ``json.dumps`` would write
them, but ``json_valid`` (the purge guards) rejects them while ``json.loads`` reads them back, so
a servable row could be treated as malformed. The writes use ``allow_nan=False`` and fail loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service.alignment_doc import AlignedRow
from app.service.alignment_store import Aligning, store_aligned
from app.service.cache_store import insert_analysis
from app.service.store import Store

from .service_helpers import SCOPE
from .test_global_retention import doc

BAD = [float("nan"), float("inf"), float("-inf")]


def _rows(store: Store, table: str) -> int:
    with store._conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("bad", BAD)
def test_an_analysis_with_a_non_finite_number_is_not_stored(tmp_path: Path, bad: float) -> None:
    store = Store(tmp_path / "svc.db")
    d = doc("a")
    d["tempo"] = {"bpm": bad}
    with pytest.raises(ValueError):
        with store._conn() as conn:
            insert_analysis(conn, "an_a", None, d, 1.0)
    assert _rows(store, "analyses") == 0


@pytest.mark.parametrize("bad", BAD)
def test_an_alignment_with_a_non_finite_number_is_not_stored(tmp_path: Path, bad: float) -> None:
    store = Store(tmp_path / "svc.db")
    row = AlignedRow("al_a", "an_a", {"offset_s": bad}, {"result": "ok"})
    with pytest.raises(ValueError):
        with store._conn() as conn:
            store_aligned(conn, Aligning(None, "k", row), 1.0, 100.0)
    assert _rows(store, "alignments") == 0


def test_an_idempotency_body_with_a_non_finite_number_is_not_stored(tmp_path: Path) -> None:
    store = Store(tmp_path / "svc.db")
    with pytest.raises(ValueError):
        store.idem_put(SCOPE, "key", "h", 200, {"x": float("nan")})
    assert _rows(store, "idempotency") == 0
