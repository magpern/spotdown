"""Container healthcheck: ``python -m spotdown_ui.healthcheck``.

Asks the UI's own ``/healthz`` on the loopback address with a plain ``http.client`` connection (no
``HTTP_PROXY`` can send it elsewhere), with the first ``UI_ALLOWED_HOSTS`` entry as its ``Host``
so the allowlist (ADR 0003) lets it in. ``/healthz`` never calls the backend. Exit 0 is healthy,
1 is not; nothing is printed.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from typing import Mapping

from .exposure_policy import (
    DEFAULT_ALLOWED_HOSTS,
    SettingsError,
    normalise_host,
    parse_allowed_hosts,
)

ADDRESS = "127.0.0.1"
PORT = 8000
TIMEOUT_S = 4.0


def probe_host(env: Mapping[str, str]) -> str:
    """The ``Host`` header the probe sends: the first allowed host, with the port."""
    raw = env.get("UI_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    first = raw.split(",", 1)[0]
    try:
        hosts = parse_allowed_hosts(raw)
    except SettingsError:
        hosts = frozenset()
    host = normalise_host(first)
    if host is None or host not in hosts:
        host = ADDRESS
    return f"{host}:{PORT}"


def healthy(env: Mapping[str, str]) -> bool:
    conn = http.client.HTTPConnection(ADDRESS, PORT, timeout=TIMEOUT_S)
    try:
        conn.request("GET", "/healthz", headers={"Host": probe_host(env)})
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        body = json.loads(resp.read(4096))
        return isinstance(body, dict) and body.get("ok") is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def main() -> int:
    return 0 if healthy(os.environ) else 1


if __name__ == "__main__":
    sys.exit(main())
