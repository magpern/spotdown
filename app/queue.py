from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from .config import get_settings

QUEUE_NAME = "spotdown"


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


@lru_cache
def get_queue() -> Queue:
    # job_timeout: conversions (download + transcode) can take a while.
    return Queue(QUEUE_NAME, connection=get_redis(), default_timeout=1800)
