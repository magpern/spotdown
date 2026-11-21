"""POST /api/v1/analyze-upload: the one slot, the byte cap, the read deadline, the order of the
cleanup, the hostile-input checks and the leftover sweep (ADR 0002, Decisions 3 and 4).

Generated audio only; the sandbox is faked except where a test says it runs the real child.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, List

import httpx
import pytest

pytest.importorskip("fcntl")

from app import db, upload_route, upload_slot  # noqa: E402
from app.main import create_app  # noqa: E402

from . import media_fixtures as mf  # noqa: E402
from .upload_helpers import (  # noqa: E402
    BASE,
    GOOD,
    LOCK,
    PATH,
    fake_sandbox,
    left,
    make_app,
    standalone_settings,
)

WAV = mf.wav(seconds=0.5)


@pytest.fixture()
def settings(tmp_path: Path):
    return standalone_settings(tmp_path)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _gated(first: bytes, gate: asyncio.Event, rest: bytes = b"") -> AsyncIterator[bytes]:
    yield first
    await gate.wait()
    if rest:
        yield rest


def _post(app: Any, body: Any = WAV, headers: Any = None) -> httpx.Response:
    async def go() -> httpx.Response:
        async with _client(app) as client:
            return await client.post(PATH, headers=headers or GOOD, content=body)

    return asyncio.run(go())


# --- the slot -----------------------------------------------------------------------------------


def test_a_second_request_mid_upload_gets_429_and_nothing_of_it_is_written(
    settings, monkeypatch
) -> None:
    fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)

    async def go() -> None:
        gate = asyncio.Event()
        async with _client(app) as client:
            first = asyncio.create_task(
                client.post(PATH, headers=GOOD, content=_gated(WAV[:100], gate, WAV[100:]))
            )
            while len(left(settings)) < 2:  # the first upload's file exists
                await asyncio.sleep(0.01)
            during = left(settings)
            second = await client.post(PATH, headers=GOOD, content=WAV)
            assert second.status_code == 429 and second.headers["Retry-After"].isdigit()
            assert left(settings) == during  # nothing of the second is written
            gate.set()
            assert (await first).status_code == 200

    asyncio.run(go())
    assert left(settings) == [LOCK]


def test_a_second_request_mid_analysis_gets_429(settings, monkeypatch) -> None:
    app = make_app(settings, monkeypatch)
    seen: List[Any] = []

    def run(audio, media, work, limits, on_start=None):
        seen.append(_post(app).status_code)  # a request while this one analyses
        from .service_helpers import fake_analyser_result

        return fake_analyser_result()

    monkeypatch.setattr(upload_route_sandbox(), "run_analysis", run)
    assert _post(app).status_code == 200
    assert seen == [429] and left(settings) == [LOCK]


def upload_route_sandbox() -> Any:
    from app.service import sandbox

    return sandbox


# --- the byte cap and the read deadline ---------------------------------------------------------


def test_an_over_size_streamed_body_is_cut_off_at_the_cap(tmp_path: Path, monkeypatch) -> None:
    settings = standalone_settings(tmp_path, max_upload_bytes=50_000)
    app = make_app(settings, monkeypatch)
    pulled: List[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield WAV[:44]
        for _ in range(1000):
            pulled.append(1)
            yield b"\x00" * 10_000

    res = _post(app, body())
    assert res.status_code == 413
    assert len(pulled) <= 7  # stopped just past the 50 kB cap
    assert left(settings) == [LOCK]


def test_a_trickled_body_past_the_deadline_gets_408_and_frees_the_slot(
    tmp_path: Path, monkeypatch
) -> None:
    fake_sandbox(monkeypatch)
    settings = standalone_settings(tmp_path, standalone_upload_read_s=0.5)
    app = make_app(settings, monkeypatch)

    async def trickle() -> AsyncIterator[bytes]:
        for i in range(0, len(WAV), 10):
            await asyncio.sleep(0.05)
            yield WAV[i : i + 10]

    assert _post(app, trickle()).status_code == 408
    assert left(settings) == [LOCK]
    assert _post(app).status_code == 200  # the slot is free


# --- the order of the cleanup -------------------------------------------------------------------


@pytest.fixture()
def release_spy(monkeypatch) -> List[List[str]]:
    """What the directory holds at each release of the slot."""
    seen: List[List[str]] = []
    real = upload_slot.Slot.release

    def release(self) -> None:
        if self.held:
            seen.append(sorted(p.name for p in self.directory.iterdir()))
        real(self)

    monkeypatch.setattr(upload_slot.Slot, "release", release)
    return seen


def _fail(*args: Any, **kwargs: Any) -> Any:
    from app.service.failure_policy import JobFailure, Reason

    raise JobFailure(Reason.DECODE)


@pytest.mark.parametrize("path", ["success", "failure", "timeout", "refused"])
def test_the_files_are_deleted_before_the_slot_is_released(
    tmp_path: Path, monkeypatch, release_spy, path: str
) -> None:
    settings = standalone_settings(tmp_path, standalone_upload_read_s=0.3)
    if path == "failure":
        monkeypatch.setattr(upload_route_sandbox(), "run_analysis", _fail)
    else:
        fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)
    release_spy.clear()  # the startup sweep's release

    async def slow() -> AsyncIterator[bytes]:
        yield WAV[:20]
        await asyncio.sleep(2)

    body = {"success": WAV, "failure": WAV, "timeout": slow(), "refused": b"OggS" + b"\0" * 99}
    code = {"success": 200, "failure": 422, "timeout": 408, "refused": 415}
    assert _post(app, body[path]).status_code == code[path]
    assert release_spy == [[LOCK]]


# --- hostile input on this route ----------------------------------------------------------------

HOSTILE = {
    "wrong_container": (b"OggS" + b"\x00" * 200, 415),
    "html": (b"<html>" * 50, 415),
    "truncated_wav": (mf.wav(seconds=1.0)[:-100], 415),
    "malformed_mp4": (b"\x00\x00\x00\x18ftypM4A " + b"PK\x03\x04" * 64, 415),
    "mp3_zip_after_frames": (mf.mp3_frames(10) + b"PK\x03\x04" + b"\x00" * 300, 415),
    "empty": (b"", 415),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_files_are_refused_by_the_shared_checks(settings, monkeypatch, name) -> None:
    calls: List[Path] = []
    fake_sandbox(monkeypatch, calls)
    data, code = HOSTILE[name]
    res = _post(make_app(settings, monkeypatch), data)
    assert res.status_code == code
    assert calls == [] and left(settings) == [LOCK]


def test_the_shared_module_is_what_refuses(settings, monkeypatch) -> None:
    """The route has no check of its own: a refusing shared check refuses a good file."""
    from app.service import hostile_input

    async def refuse(*args: Any) -> str:
        raise hostile_input.Refused(*hostile_input.MALFORMED)

    monkeypatch.setattr(hostile_input, "check_file", refuse)
    fake_sandbox(monkeypatch)
    assert _post(make_app(settings, monkeypatch)).status_code == 415


def test_an_over_length_file_is_refused_by_the_real_sandbox(settings, monkeypatch) -> None:
    data = mf.mp3_frames(20_000, version=0, br=1, sr=2)  # 24 minutes in 1.4 MB, cap 60 s
    res = _post(make_app(settings, monkeypatch), data)
    assert res.status_code == 422 and res.json()["detail"] == "the audio is longer than the limit"
    assert left(settings) == [LOCK]


def test_a_real_analysis_returns_no_tag_no_name_and_no_audio(
    tmp_path: Path, settings, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    data = mf.id3v2() + mf.real_mp3(tmp_path / "x.mp3", seconds=4.0).read_bytes()
    app = make_app(settings, monkeypatch)
    headers = {**GOOD, "Content-Disposition": f'attachment; filename="{mf.MARKER}.mp3"'}
    res = _post(app, data, headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["track_id"].startswith("upl_") and body["bpm"] > 0
    assert mf.MARKER not in res.text and mf.MARKER not in caplog.text
    assert "audio" not in {k.lower() for k in body}
    assert left(settings) == [LOCK]
    cached = _post(app, data)  # the same bytes again: the cached result
    assert cached.status_code == 200 and cached.json() == body


def test_a_repeat_of_the_same_bytes_is_a_cache_hit(settings, monkeypatch) -> None:
    calls: List[Path] = []
    fake_sandbox(monkeypatch, calls)
    app = make_app(settings, monkeypatch)
    first, again = _post(app), _post(app)
    assert first.status_code == again.status_code == 200 and len(calls) == 1
    assert first.json()["track_id"] == again.json()["track_id"]
    import hashlib

    sha = hashlib.sha256(WAV).hexdigest()
    assert first.json()["track_id"] == "upl_" + sha
    from app.analyze import ANALYZER_VERSION, V1_RESPONSE_VERSION

    row = db.get_analysis("upl_" + sha, ANALYZER_VERSION, V1_RESPONSE_VERSION)
    assert row is not None and json.loads(row)["track_id"] == "upl_" + sha


# --- leftovers and the lock file ----------------------------------------------------------------


def test_planted_leftovers_are_deleted_when_the_slot_is_taken(settings, monkeypatch) -> None:
    fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)
    d = settings.standalone_uploads_dir
    (d / "up_old").write_bytes(b"x")
    (d / "up_old.work").mkdir()
    (d / "up_old.work" / "result.json").write_text("{}")
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    (settings.files_dir / "keep.mp3").write_bytes(b"x")
    (d / "link").symlink_to(settings.files_dir)  # removed, never followed

    async def go() -> None:
        gate = asyncio.Event()
        async with _client(app) as client:
            task = asyncio.create_task(
                client.post(PATH, headers=GOOD, content=_gated(WAV[:100], gate, WAV[100:]))
            )
            while not any(n.startswith("up_") and len(n) == 35 for n in left(settings)):
                await asyncio.sleep(0.01)
            # Taking the slot swept them: only the lock file and this request's own file remain.
            assert len(left(settings)) == 2 and "up_old" not in left(settings)
            gate.set()
            assert (await task).status_code == 200

    asyncio.run(go())
    assert left(settings) == [LOCK] and (settings.files_dir / "keep.mp3").is_file()


def test_the_startup_sweep_deletes_leftovers_but_never_the_lock(settings, monkeypatch) -> None:
    make_app(settings, monkeypatch)
    lock = settings.standalone_uploads_dir / LOCK
    inode = lock.stat().st_ino
    (settings.standalone_uploads_dir / "up_crashed").write_bytes(b"x")
    create_app(settings)
    assert left(settings) == [LOCK] and lock.stat().st_ino == inode


def test_a_startup_sweep_while_another_worker_uploads_deletes_nothing(
    settings, monkeypatch
) -> None:
    fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)
    lock = settings.standalone_uploads_dir / LOCK
    inode = lock.stat().st_ino

    async def go() -> None:
        gate = asyncio.Event()
        async with _client(app) as client:
            first = asyncio.create_task(
                client.post(PATH, headers=GOOD, content=_gated(WAV[:100], gate, WAV[100:]))
            )
            while len(left(settings)) < 2:
                await asyncio.sleep(0.01)
            during = left(settings)
            other = create_app(settings)  # another worker starts up
            assert left(settings) == during and lock.stat().st_ino == inode
            assert other is not None
            gate.set()
            assert (await first).status_code == 200

    asyncio.run(go())
    assert left(settings) == [LOCK] and lock.stat().st_ino == inode


def test_the_sweep_never_runs_without_the_slot(settings) -> None:
    settings.standalone_uploads_dir.mkdir(parents=True)
    slot = upload_slot.try_acquire(settings.standalone_uploads_dir)
    assert slot is not None
    assert upload_slot.try_acquire(settings.standalone_uploads_dir) is None  # one holder
    assert upload_slot.startup_sweep(settings.standalone_uploads_dir) is False
    slot.release()
    with pytest.raises(RuntimeError):
        slot.sweep()


def test_no_file_name_or_tag_in_responses_or_logs(settings, monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)
    named = {**GOOD, "Content-Disposition": f'attachment; filename="{mf.MARKER}.wav"'}
    for body in (mf.wav(chunks=[mf.info_chunk(mf.MARKER)]), b"OggS" + mf.MARKER.encode() * 9):
        res = _post(app, body, named)
        assert mf.MARKER not in res.text
    assert mf.MARKER not in caplog.text
    assert upload_route.UPLOAD_PREFIX == "upl_"


def test_a_failed_delete_is_logged_and_the_slot_is_still_released(
    settings, monkeypatch, caplog
) -> None:
    fake_sandbox(monkeypatch)
    app = make_app(settings, monkeypatch)
    import shutil

    calls: List[Path] = []
    real_rmtree = shutil.rmtree

    def rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(Path(path))
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "rmtree", rmtree)
    with caplog.at_level(logging.ERROR, logger="spotdown.upload_route"):
        assert _post(app).status_code == 200
    assert calls and "could not be emptied" in caplog.text
    assert sorted(n for n in left(settings) if not n.endswith(".work")) == [LOCK]
    slot = upload_slot.try_acquire(settings.standalone_uploads_dir)  # released all the same
    assert slot is not None
    slot.release()
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    assert _post(app).status_code == 200  # the next holder's sweep retries the leftover
    assert left(settings) == [LOCK]
