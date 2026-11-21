"""The shared declared-length check (``hostile_input.declared_too_large``) and the same check
elsewhere: a ``Content-Length`` must be plain ASCII digits. ``str.isdigit`` alone accepts ``²``
and other digits that ``int`` refuses, which would turn a refusal into a 500."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.service import hostile_input

from .standalone_helpers import BASE, LOGIN_HEADERS, Standalone

NOT_PLAIN = ["²", "1²", "١٢", "1e3", "-1", "+1", " 1", "1 ", "", "0x10"]


@pytest.mark.parametrize("value", NOT_PLAIN)
def test_anything_but_plain_ascii_digits_counts_as_too_large(value: str) -> None:
    assert hostile_input.declared_too_large(value, 10**9) is True


@pytest.mark.parametrize("value,cap,over", [("0", 1, False), ("10", 10, False), ("11", 10, True)])
def test_plain_digits_are_compared_with_the_cap(value: str, cap: int, over: bool) -> None:
    assert hostile_input.declared_too_large(value, cap) is over
    assert hostile_input.declared_too_large(None, cap) is False


def test_a_superscript_length_on_login_is_400_not_500(tmp_path: Path, monkeypatch) -> None:
    sa = Standalone(tmp_path, monkeypatch)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=sa.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            headers = {**LOGIN_HEADERS, "Content-Length": b"\xb2"}  # "²" in latin-1
            return await client.post("/login", headers=headers, content=b"{}")

    assert asyncio.run(go()).status_code == 400


def test_a_superscript_length_from_a_url_source_is_not_a_crash(tmp_path: Path) -> None:
    from .test_url_fetch import fetch

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[(b"content-length", b"\xb2")],
            stream=httpx.ByteStream(b"RIFF....WAVEdata"),
        )

    got = fetch(tmp_path, handler)  # the declared length is ignored; the stream is capped
    assert got.bytes == 16
