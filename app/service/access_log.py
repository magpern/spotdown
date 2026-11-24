"""Keeps query strings out of uvicorn's access log.

uvicorn logs each request line, query string included, at INFO. A query can carry a caller's
URL (a lookup's ``source=url:...``, which may be a signed URL) or a scope, so the path is logged
without it. Callers are told to look up a ``url`` source by its digest (docs/contract-v2.md),
and this filter makes a raw URL sent anyway leak nowhere.
"""

from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"
_PATH_ARG = 2  # uvicorn: (client, method, path with query, http version, status)


class QueryRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > _PATH_ARG:
            path = args[_PATH_ARG]
            if isinstance(path, str):
                bare = path.split("?", 1)[0]
                record.args = args[:_PATH_ARG] + (bare,) + args[_PATH_ARG + 1 :]
        return True


def install() -> None:
    """Adds the filter to the access logger once (idempotent across app factories)."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, QueryRedactingFilter) for f in logger.filters):
        logger.addFilter(QueryRedactingFilter())
