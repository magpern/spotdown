"""A cache hit whose analysis is purged between the lookup and the job's insert (#67 review): the
job is never recorded ``done`` on a missing id. ``add_job_done`` reads the analysis in its own
transaction, with or without a reference; when it has gone nothing is stored and the request
is queued and analysed again."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import pytest

from app.service.store import Store

from .cache_helpers import Stack, sha
from .service_helpers import AUTH, SCOPE

NEWER = "9.9.9.newer"


def test_a_done_job_is_never_inserted_on_a_missing_analysis(tmp_path: Path) -> None:
    store = Store(tmp_path / "service.db")
    job = {"job_id": "job_hit", "cache_scope": SCOPE, "source_kind": "upload"}
    assert store.add_job_done(job, "an_gone", SCOPE) is None
    assert store.get_job("job_hit") is None


def test_a_hit_purged_before_the_insert_is_analysed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Stack(tmp_path)
    data = stack.audio("a.wav")
    first = stack.analysis_id(stack.analyse_bytes(data))
    real = Store.add_job_done

    def purged_meanwhile(self: Store, job: Any, hit_id: str, scope: Optional[str]) -> Any:
        # a worker of a newer analyzer version purges the entry after the route's lookup
        self.purge_scoped(time.time(), NEWER, 90 * 86400)
        assert self.get_analysis(hit_id) is None
        return real(self, job, hit_id, scope)

    monkeypatch.setattr(Store, "add_job_done", purged_meanwhile)
    again = stack.analyse_bytes(data)
    assert again.status_code == 202 and again.json()["status"] == "queued"
    assert stack.analysed == [sha(data), sha(data)]  # analysed again, not done on the lost id
    job = stack.client.get(
        f"/v2/jobs/{again.json()['job_id']}?cache_scope={SCOPE}", headers=AUTH
    ).json()
    assert job["status"] == "done" and job["analysis_id"] != first
    assert stack.get_analysis(job["analysis_id"]).status_code == 200
