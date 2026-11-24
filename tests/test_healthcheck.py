"""``python -m app.healthcheck`` (spotdown#66): the image's container healthcheck. The api check
talks to a loopback fake server; the worker check gets a fake Redis. No real network."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator, Tuple

import pytest

from app import healthcheck


def _serve(status: int, body: bytes) -> Tuple[HTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status if self.path == "/healthz" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def server(request: pytest.FixtureRequest) -> Iterator[int]:
    status, body = request.param
    srv, thread = _serve(status, body)
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        thread.join(5)
        srv.server_close()


@pytest.mark.parametrize(
    "server, healthy",
    [
        ((200, json.dumps({"ok": True, "redis": False}).encode()), True),
        ((200, json.dumps({"ok": False}).encode()), False),
        ((200, b"not json"), False),
        ((200, b"[true]"), False),
        ((503, json.dumps({"ok": True}).encode()), False),
    ],
    indirect=["server"],
)
def test_api_check_reads_status_and_body(server: int, healthy: bool) -> None:
    assert healthcheck.api_healthy(port=server, timeout=2) is healthy


def test_api_check_is_unhealthy_when_nothing_listens() -> None:
    srv, thread = _serve(200, b"{}")
    port = srv.server_address[1]
    srv.shutdown()
    thread.join(5)
    srv.server_close()
    assert healthcheck.api_healthy(port=port, timeout=1) is False


def test_api_check_ignores_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    for name in ("NO_PROXY", "no_proxy"):  # a host's NO_PROXY=127.0.0.1 would hide a proxy use
        monkeypatch.delenv(name, raising=False)
    srv, thread = _serve(200, b'{"ok": true}')
    try:
        assert healthcheck.api_healthy(port=srv.server_address[1], timeout=2) is True
    finally:
        srv.shutdown()
        thread.join(5)
        srv.server_close()


class _Redis:
    def __init__(self, result: object) -> None:
        self.result = result

    def ping(self) -> bool:
        if isinstance(self.result, Exception):
            raise self.result
        return bool(self.result)


@pytest.mark.parametrize(
    "result, healthy", [(True, True), (False, False), (ConnectionError("down"), False)]
)
def test_worker_check_pings_redis(
    monkeypatch: pytest.MonkeyPatch, result: object, healthy: bool
) -> None:
    from app import queue

    monkeypatch.setattr(queue, "get_redis", lambda: _Redis(result))
    assert healthcheck.worker_healthy() is healthy


def test_main_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(healthcheck.CHECKS, "api", lambda: True)
    monkeypatch.setitem(healthcheck.CHECKS, "worker", lambda: False)
    assert healthcheck.main([]) == 0
    assert healthcheck.main(["api"]) == 0
    assert healthcheck.main(["worker"]) == 1
    assert healthcheck.main(["other"]) == 2
    assert healthcheck.main(["api", "extra"]) == 2
