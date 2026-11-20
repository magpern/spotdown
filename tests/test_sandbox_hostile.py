"""Hostile files in the analysis sandbox: checked before decoding, a decode deadline, no tags out.

The in-process tests call ``sandbox_child.run`` directly (its alarm stubbed); the others run the
real sandbox subprocess through ``execute_job``, so a file that slipped past the upload check (or
came from the url or youtube source) still ends ``processing_failed`` with nothing left behind.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import soundfile as sf

from app.config import Settings
from app.service import sandbox
from app.service import sandbox_child as child
from app.service.routes import job_view
from app.service.store import Store
from app.service.uploads import upload_path
from app.service.worker import execute_job

from . import media_fixtures as mf
from .service_helpers import AUTH, SCOPE, assert_valid, audio_files, service_settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = service_settings(tmp_path)
    s.ensure_dirs()
    return s


@pytest.fixture()
def store(settings: Settings) -> Store:
    return Store(settings.service_db_path)


@pytest.fixture()
def alarm(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """The child's decode alarm, recorded instead of armed (it would end the test process)."""
    armed: List[float] = []
    monkeypatch.setattr(child.signal, "signal", lambda *a: None)
    monkeypatch.setattr(child.signal, "setitimer", lambda which, s: armed.append(s))
    return armed


def _no_decode(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the decoder was reached")


def write_wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    sf.write(str(path), mf.tone(seconds, rate), rate, subtype="PCM_16")
    return path


def test_a_malformed_file_never_reaches_the_decoder(tmp_path, alarm, monkeypatch) -> None:
    monkeypatch.setattr(child.decode, "decode", _no_decode)
    audio = tmp_path / "in"
    audio.write_bytes(mf.wav()[:-10])
    assert child.run(audio, tmp_path / "r.json", 1200.0, "wav", 60.0) == child.EXIT_INVALID_MEDIA
    assert alarm == [60.0, 0]  # armed for the checks and the decode, then disarmed


def test_an_overlong_container_is_refused_before_decoding(tmp_path, alarm, monkeypatch) -> None:
    monkeypatch.setattr(child.decode, "decode", _no_decode)
    audio = tmp_path / "in"
    audio.write_bytes(mf.mp3_frames(20_000, version=0, br=1, sr=2))  # 24 minutes in 1.4 MB
    assert child.run(audio, tmp_path / "r.json", 60.0, "mp3", 60.0) == child.EXIT_TOO_LONG


def test_a_decoder_timeout_is_its_own_exit(tmp_path, alarm, monkeypatch) -> None:
    def slow(*args: Any, **kwargs: Any) -> Any:
        raise child.decode.DecodeTimeout()

    monkeypatch.setattr(child.decode, "decode", slow)
    audio = write_wav(tmp_path / "in.wav", 1.0)
    assert child.run(audio, tmp_path / "r.json", 60.0, "wav", 1.0) == child.EXIT_DECODE_TIMEOUT


def test_a_zero_decode_deadline_is_a_usage_error(tmp_path) -> None:
    assert child.main([str(tmp_path / "a"), str(tmp_path / "r"), "60", "wav", "0"]) == 2


def test_the_probe_parses_no_tags(tmp_path, alarm, monkeypatch) -> None:
    """mutagen's tag readers would parse the ID3 frame; only its stream info is used."""
    import mutagen
    import mutagen.id3

    def no_tags(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a tag reader was used")

    monkeypatch.setattr(mutagen, "File", no_tags)
    monkeypatch.setattr(mutagen.id3.ID3, "load", no_tags)
    audio = tmp_path / "in"
    audio.write_bytes(mf.id3v2() + mf.real_mp3(tmp_path / "a.mp3").read_bytes())
    result = tmp_path / "r.json"
    assert child.run(audio, result, 60.0, "mp3", 60.0) == child.EXIT_OK
    payload = result.read_text(encoding="utf-8")
    assert mf.MARKER not in payload
    probe = json.loads(payload)["probe"]
    assert probe["codec"] == "mp3" and probe["sample_rate_hz"] == 22050
    assert probe["channels"] == 1 and probe["bitrate_bps"] > 0


# ---- the real sandbox subprocess ----


def stored_job(settings: Settings, store: Store, data: bytes, media: str, job_id: str) -> str:
    """An upload already kept (as if it had passed the upload check), and its job."""
    upload_id = "up_" + job_id
    upload_path(settings, upload_id).write_bytes(data)
    store.add_upload(upload_id, SCOPE, "a" * 64, len(data), media, 3600)
    assert store.claim_upload(upload_id, SCOPE) is not None
    store.add_job(
        {"job_id": job_id, "cache_scope": SCOPE, "source_kind": "upload", "upload_id": upload_id}
    )
    return job_id


def finished(settings: Settings, store: Store, job_id: str) -> Dict[str, Any]:
    execute_job(job_id, settings, store)
    job = store.get_job(job_id)
    assert job is not None
    out = job_view(job)
    assert_valid("job-v1", out)
    assert audio_files(settings) == [] and not (settings.work_dir / job_id).exists()
    assert store.get_upload("up_" + job_id) is None
    return out


HOSTILE = {
    "truncated_wav": (mf.wav(seconds=2.0)[:-100], "wav"),
    "polyglot_wav_zip": (mf.wav() + b"PK\x03\x04" + b"\x00" * 64, "wav"),
    "insane_rate_wav": (mf.wav(rate=4000), "wav"),
    "html_behind_id3": (mf.id3v2() + b"<html>" * 100, "mp3"),
    "zip_behind_mp4_header": (b"\x00\x00\x00\x18ftypM4A " + b"PK\x03\x04" * 64, "mp4"),
    "zero_length": (b"", "wav"),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_files_fail_cleanly_in_the_real_sandbox(settings, store, name: str) -> None:
    data, media = HOSTILE[name]
    out = finished(settings, store, stored_job(settings, store, data, media, "job_h"))
    assert (out["status"], out["outcome"], out["failure_kind"]) == (
        "failed",
        "processing_failed",
        "decode_error",
    )


def test_the_decode_deadline_ends_the_child_as_a_timeout(settings, store, tmp_path) -> None:
    settings.analysis_max_decode_s = 1e-6
    data = write_wav(tmp_path / "a.wav", 3.0).read_bytes()
    out = finished(settings, store, stored_job(settings, store, data, "wav", "job_t"))
    assert (out["outcome"], out["failure_kind"]) == ("processing_failed", "timeout")


def test_a_low_bitrate_bomb_ends_as_out_of_memory(settings, store) -> None:
    settings.analysis_max_decoded_s = 30.0
    data = mf.mp3_frames(2_000, version=0, br=1, sr=2)  # 144 s
    out = finished(settings, store, stored_job(settings, store, data, "mp3", "job_b"))
    assert (out["outcome"], out["failure_kind"]) == ("processing_failed", "out_of_memory")


@pytest.mark.parametrize("media", ["wav", "mp3"])
def test_tags_never_reach_the_analysis(settings, store, tmp_path, media: str) -> None:
    if media == "wav":
        wav = write_wav(tmp_path / "a.wav", 6.0).read_bytes()
        riff = 4 + len(mf.info_chunk(mf.MARKER)) + len(wav) - 12
        data = b"RIFF" + riff.to_bytes(4, "little") + b"WAVE" + mf.info_chunk(mf.MARKER)
        data += wav[12:]
    else:
        data = mf.id3v2() + mf.real_mp3(tmp_path / "a.mp3", seconds=6.0).read_bytes()
    out = finished(settings, store, stored_job(settings, store, data, media, "job_tag"))
    assert out["status"] == "done", out
    doc = store.get_analysis(out["analysis_id"])
    assert_valid("analysis-v1", doc)
    assert mf.MARKER not in json.dumps(doc) and mf.MARKER not in json.dumps(out)
    assert mf.MARKER.encode() not in settings.service_db_path.read_bytes()


# ---- the other entry points: the url and youtube sources reach the same checks ----


def _no_audio_left(stack) -> None:
    assert audio_files(stack.settings) == []
    assert not list((stack.settings.data_dir / "uploads").glob("*"))


@pytest.mark.parametrize("name", ["polyglot_wav_zip", "zip_behind_mp4_header", "html_behind_id3"])
def test_a_hostile_url_source_fails_cleanly(tmp_path: Path, name: str) -> None:
    from .cache_helpers import Stack

    stack = Stack(tmp_path)
    stack._analyser = sandbox.run_analysis  # type: ignore[method-assign]
    stack.fetch_bytes = HOSTILE[name][0]
    res = stack.post({"cache_scope": SCOPE, "url": "https://media.example.com/a/t.wav"})
    job = stack.client.get(
        f"/v2/jobs/{res.json()['job_id']}", headers=AUTH, params={"cache_scope": SCOPE}
    ).json()
    assert (job["outcome"], job["failure_kind"]) == ("processing_failed", "decode_error")
    _no_audio_left(stack)


def test_a_hostile_youtube_download_fails_cleanly(tmp_path: Path) -> None:
    from .youtube_fakes import YoutubeStack

    stack = YoutubeStack(tmp_path)
    stack._analyser = sandbox.run_analysis  # type: ignore[method-assign]
    stack.world.audio = HOSTILE["zip_behind_mp4_header"][0]
    job = stack.job(stack.post({"source_ref": "youtube:dQw4w9WgXcQ"}))
    assert (job["outcome"], job["failure_kind"]) == ("processing_failed", "decode_error")
    assert stack.store.get_analysis(job.get("analysis_id") or "") is None
    _no_audio_left(stack)


# ---- every new handler of the child, with each step made to raise ----


@pytest.mark.parametrize("step", ["inspect", "probe", "decode"])
@pytest.mark.parametrize("error,code", [(RuntimeError, 10), (MemoryError, 12)])
def test_each_step_raising_ends_as_its_exit(
    tmp_path, alarm, monkeypatch, step, error, code
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise error("x")

    target = {
        "inspect": (child.container_io, "inspect_file"),
        "probe": (child, "_probe"),
        "decode": (child.decode, "decode"),
    }[step]
    monkeypatch.setattr(*target, boom)
    audio = write_wav(tmp_path / "in.wav", 1.0)
    assert child.run(audio, tmp_path / "r.json", 60.0, "wav", 60.0) == code
    assert alarm[-1] == 0  # disarmed on every path


def test_an_ffmpeg_overrun_is_a_decode_timeout_not_the_alarm(tmp_path, alarm, monkeypatch) -> None:
    """The child's ffmpeg is stopped just before the alarm, so the timeout exit is reachable."""
    script = tmp_path / "ffmpeg"
    script.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(child.decode.shutil, "which", lambda name: str(script))
    audio = tmp_path / "in"
    audio.write_bytes(mf.mp4())
    code = child.run(audio, tmp_path / "r.json", 60.0, "mp4", 1.5)
    assert code == child.EXIT_DECODE_TIMEOUT


@pytest.mark.parametrize(
    "code,reason",
    [
        (child.EXIT_INVALID_MEDIA, "unsupported_media"),
        (child.EXIT_DECODE_TIMEOUT, "deadline"),
        (-14, "deadline"),  # SIGALRM: the child's decode alarm
        (child.EXIT_TOO_LONG, "too_long"),
        (child.EXIT_DECODE, "decode"),
    ],
)
def test_the_childs_exits_map_to_their_reasons(code: int, reason: str) -> None:
    limits = sandbox.Limits(memory_bytes=1, cpu_s=10, wall_s=10.0, decoded_s=60.0, decode_s=5.0)
    assert sandbox._reason_for(code, 0.0, limits).value == reason
