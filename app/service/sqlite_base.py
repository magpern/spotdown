"""One SQLite file, one short-lived connection per call, and the lifetime of its global
reference rows (``GLOBAL_REFERENCE_TTL_S``, ADR 0040 §7)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .cache_policy import GLOBAL_REFERENCE_TTL_S


class SqliteBase:
    def __init__(self, path: Path, global_ttl_s: float = GLOBAL_REFERENCE_TTL_S) -> None:
        self.path = path
        self.global_ttl_s = float(global_ttl_s)
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
