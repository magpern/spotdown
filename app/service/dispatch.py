"""Hands a queued v2 job to the RQ worker. Only the job id travels through Redis."""

from __future__ import annotations

from typing import Callable

from ..config import Settings

Dispatch = Callable[[str], None]


def rq_dispatcher(settings: Settings) -> Dispatch:
    def dispatch(job_id: str) -> None:
        from ..queue import get_queue

        get_queue().enqueue(
            "app.service.worker.run_job",
            job_id,
            job_id=job_id,
            job_timeout=int(settings.analysis_wall_timeout_s + settings.url_fetch_timeout_s + 60),
            result_ttl=0,
            failure_ttl=24 * 3600,
        )

    return dispatch
