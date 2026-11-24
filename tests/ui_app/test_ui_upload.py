"""``PUT /api/uploads`` streamed to the backend (spotdown#78 acceptance 7; ADR 0002 in the UI
image): over the cap it stops at the cap with a clear error and reads no more; a slow body hits
the read deadline; one slot."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List

import pytest

from .helpers import BASE, UI_HEADERS, Ui

OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture
def ui(tmp_path) -> Ui:
    return Ui(tmp_path, MAX_UPLOAD_BYTES="1000", UI_UPLOAD_READ_S="0.5")


def test_a_declared_length_over_the_cap_is_413_before_anything_is_sent(ui) -> None:
    with ui.signed() as client:
        res = client.put("/api/uploads", content=b"x" * 1001, headers=OCTET)
    assert res.status_code == 413 and res.json()["error"]["code"] == "too_large"
    assert ui.backend.seen == []


def test_a_body_at_the_cap_goes_through(ui) -> None:
    with ui.signed() as client:
        res = client.put("/api/uploads", content=b"x" * 1000, headers=OCTET)
    assert res.status_code == 201 and res.json()["bytes"] == 1000


def _cookie(ui: Ui) -> str:
    with ui.signed() as client:
        return client.headers["Cookie"]


def _scope(ui: Ui, extra: List[tuple]) -> Dict[str, Any]:
    headers = [(b"host", b"localhost:8000"), (b"origin", BASE.encode()),
               (b"x-spotdown-ui", b"1"), (b"content-type", b"application/octet-stream"),
               (b"cookie", _cookie(ui).encode())] + extra  # fmt: skip
    return {
        "type": "http",
        "method": "PUT",
        "path": "/api/uploads",
        "raw_path": b"/api/uploads",
        "headers": headers,
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "http_version": "1.1",
        "server": ("localhost", 8000),
        "client": ("127.0.0.1", 1),
    }


def _drive(app, scope, receive) -> List[dict]:
    sent: List[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _answer(sent: List[dict]) -> tuple:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return sent[0]["status"], json.loads(body)


def test_an_undeclared_body_over_the_cap_stops_at_the_cap(ui) -> None:
    """Chunked (no Content-Length): 300 bytes at a time. The fourth chunk passes the cap; it is
    never sent on, and no further chunk is asked for."""
    asked = []

    async def receive() -> dict:
        asked.append(1)
        return {"type": "http.request", "body": b"y" * 300, "more_body": True}

    status, body = _answer(_drive(ui.app, _scope(ui, []), receive))
    assert status == 413 and body["error"]["code"] == "too_large"
    assert len(asked) == 4
    sent = [s for s in ui.backend.seen if s.path == "/v2/uploads"]
    assert len(sent) <= 1 and all(len(s.body) <= 1000 for s in sent)


def test_a_slow_body_hits_the_deadline(ui) -> None:
    calls = []

    async def receive() -> dict:
        calls.append(1)
        if len(calls) > 1:
            await asyncio.sleep(5)
        return {"type": "http.request", "body": b"z" * 10, "more_body": True}

    start = time.monotonic()
    status, body = _answer(_drive(ui.app, _scope(ui, []), receive))
    assert status == 408 and body["error"]["code"] == "upload_timeout"
    assert time.monotonic() - start < 4


def test_a_trickling_body_hits_the_deadline_for_the_whole_body(ui) -> None:
    """Each chunk is quick, but together they take longer than the deadline."""

    async def receive() -> dict:
        await asyncio.sleep(0.1)
        return {"type": "http.request", "body": b"z", "more_body": True}

    status, body = _answer(_drive(ui.app, _scope(ui, []), receive))
    assert status == 408


def test_a_client_that_goes_away_is_400(ui) -> None:
    async def receive() -> dict:
        return {"type": "http.disconnect"}

    status, body = _answer(_drive(ui.app, _scope(ui, []), receive))
    assert status == 400 and body["error"]["code"] == "client_gone"


def test_one_slot_a_second_upload_is_429_and_not_read(ui) -> None:
    """While one upload streams, a second one is refused with Retry-After before its body."""
    from spotdown_ui import upload_proxy

    results: Dict[str, Any] = {}

    async def main() -> None:
        release = asyncio.Event()
        second_read: List[int] = []

        async def slow() -> dict:
            await release.wait()
            return {"type": "http.request", "body": b"a", "more_body": False}

        async def second() -> dict:
            second_read.append(1)
            return {"type": "http.request", "body": b"b", "more_body": False}

        sent1: List[dict] = []
        sent2: List[dict] = []

        async def send1(m: dict) -> None:
            sent1.append(m)

        async def send2(m: dict) -> None:
            sent2.append(m)

        first = asyncio.create_task(ui.app(_scope(ui, []), slow, send1))
        await asyncio.sleep(0.05)
        await ui.app(_scope(ui, []), second, send2)
        release.set()
        await first
        results.update(first=sent1, second=sent2, read=second_read)

    asyncio.run(main())
    assert results["second"][0]["status"] == 429
    assert (
        dict(results["second"][0]["headers"])[b"retry-after"]
        == str(upload_proxy.RETRY_AFTER_S).encode()
    )
    assert results["read"] == []
    assert results["first"][0]["status"] == 201


def test_the_slot_is_free_again_after_a_refused_upload(ui) -> None:
    with ui.signed() as client:
        assert client.put("/api/uploads", content=b"x" * 1001, headers=OCTET).status_code == 413
        assert client.put("/api/uploads", content=b"x", headers=OCTET).status_code == 201

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    _drive(ui.app, _scope(ui, []), receive)
    with ui.signed() as client:
        assert (
            client.put("/api/uploads", content=b"x", headers={**OCTET, **UI_HEADERS}).status_code
            == 201
        )


def _chunks(*parts: bytes):
    """A receive that sends ``parts`` and then ends the body."""
    queue = list(parts)

    async def receive() -> dict:
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    return receive


def _stalled():
    calls: List[int] = []

    async def receive() -> dict:
        calls.append(1)
        if len(calls) > 1:
            await asyncio.sleep(5)
        return {"type": "http.request", "body": b"z", "more_body": True}

    return receive


async def _gone() -> dict:
    return {"type": "http.disconnect"}


def _backend_error(ui: Ui):
    import httpx

    ui.backend.overrides[("PUT", "/v2/uploads")] = httpx.Response(
        415, json={"error": {"code": "unsupported_media_type", "message": "x"}}
    )
    return _chunks(b"abc")


def _backend_down(ui: Ui):
    import httpx

    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    ui.backend.transport.handler = down  # type: ignore[attr-defined]
    return _chunks(b"abc")


PATHS = {
    "success": (lambda ui: _chunks(b"abc", b"def"), 201),
    "backend error": (_backend_error, 415),
    "backend unreachable": (_backend_down, 502),
    "size cap": (lambda ui: _chunks(*[b"y" * 300] * 5), 413),
    "deadline": (lambda ui: _stalled(), 408),
    "client disconnect": (lambda ui: _gone, 400),
}


@pytest.mark.parametrize("path", list(PATHS))
def test_the_slot_is_held_for_the_upload_and_released_on_every_path(ui, path) -> None:
    """The slot is taken before the first body byte, so a second upload during it is 429; once
    the relayed PUT /v2/uploads has answered or failed, it is free again."""
    make, want = PATHS[path]
    receive = make(ui)
    held: Dict[str, Any] = {}

    async def main() -> None:
        async def watching() -> dict:  # a second upload while the first is reading its body
            if "second" not in held:
                held["second"] = []

                async def second_send(m: dict) -> None:
                    held["second"].append(m)

                await ui.app(_scope(ui, []), _gone, second_send)
            return await receive()

        sent: List[dict] = []

        async def send(m: dict) -> None:
            sent.append(m)

        await ui.app(_scope(ui, []), watching, send)
        held["first"] = sent

    asyncio.run(main())
    assert held["first"][0]["status"] == want, path
    assert held["second"][0]["status"] == 429, path
    ui.backend.transport.handler = ui.backend.handle  # type: ignore[attr-defined]
    ui.backend.overrides.clear()
    status, _ = _answer(_drive(ui.app, _scope(ui, []), _chunks(b"ok")))
    assert status == 201, path  # released
