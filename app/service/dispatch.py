"""Hands a queued v2 job to the RQ worker. Only the job id travels through Redis."""

from __future__ import annotations

from typing import Callable

from ..config import Settings

Dispatch = Callable[[str], None]
# How long a job may stay ``running`` beyond RQ's own timeout before the sweep fails it.
LEASE_MARGIN_S = 240


def job_timeout_s(settings: Settings) -> int:
    """RQ's timeout for one job: the fetch and the analysis, plus a minute."""
    return int(settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s + 60)


def job_lease_s(settings: Settings) -> float:
    """After this long ``running``, a job's worker is gone (``sweep``)."""
    return float(job_timeout_s(settings) + LEASE_MARGIN_S)


def rq_dispatcher(settings: Settings) -> Dispatch:
    def dispatch(job_id: str) -> None:
        from ..queue import get_queue

        get_queue().enqueue(
            "app.service.worker.run_job",
            job_id,
            job_id=job_id,
            job_timeout=job_timeout_s(settings),
            result_ttl=0,
            failure_ttl=24 * 3600,
        )

    return dispatch
