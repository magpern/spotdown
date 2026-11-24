"""Container healthcheck: ``python -m app.healthcheck [api|worker]``.

``api`` (the default, the image's ``HEALTHCHECK``) asks the server's own ``/healthz`` on the
loopback address with a plain ``http.client`` connection, so no ``HTTP_PROXY`` setting can send it
anywhere else. ``worker`` (the deploy compose file sets it on the worker) pings Redis at
``REDIS_URL``. Exit 0 is healthy, 1 is not. Nothing is printed: a probe never echoes a URL or a
response body into ``docker inspect``.
"""

from __future__ import annotations

import http.client
import json
import sys
from typing import Callable, List, Optional

HOST = "127.0.0.1"
PORT = 8000
TIMEOUT_S = 4.0


def api_healthy(host: str = HOST, port: int = PORT, timeout: float = TIMEOUT_S) -> bool:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        body = json.loads(resp.read(4096))
        return isinstance(body, dict) and body.get("ok") is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def worker_healthy() -> bool:
    try:
        from .queue import get_redis

        return bool(get_redis().ping())
    except Exception:  # noqa: BLE001 - any failure is "unhealthy", never a traceback
        return False


CHECKS = {"api": api_healthy, "worker": worker_healthy}


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    name = args[0] if args else "api"
    check: Optional[Callable[[], bool]] = CHECKS.get(name)
    if check is None or len(args) > 1:
        return 2
    return 0 if check() else 1


if __name__ == "__main__":
    sys.exit(main())
