"""The worker side of ``POST /v2/audio``: the real transcode of a generated tone, its arguments,
reuse, retention with a fake clock, and the size cap. yt-dlp is faked; no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.service import audio_files, sweep
from app.service.audio_job import execute_audio_job
from app.service.worker import execute_job, limits_from
from app.transcode import mp3_argv

from .audio_helpers import REF, VIDEO, AudioStack
from .media_fixtures import has_ffmpeg, real_m4a, real_mp3
from .service_helpers import audio_files as temp_audio
from .youtube_fakes import M4A

needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg is not installed")


@pytest.fixture()
def stack(tmp_path) -> AudioStack:
    return AudioStack(tmp_path)


@needs_ffmpeg
@pytest.mark.parametrize("bitrate", [128, 320])
def test_a_faked_download_becomes_a_tagged_mp3_at_the_bitrate(tmp_path, bitrate: int) -> None:
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3

    stack = AudioStack(tmp_path)
    stack.world.audio = real_m4a(tmp_path / "tone.m4a", seconds=2.0).read_bytes()
    stack.dispatch.then = lambda job_id: execute_job(
        job_id, stack.settings, stack.store, youtube=stack.world.client()
    )  # the worker's own entry point, with the real sandboxed transcode
    body = {"source_ref": REF, "bitrate": bitrate, "title": "Tone\x07", "artists": ["A", "B"]}
    audio_id = stack.audio_id(stack.request(body))
    got = stack.fetch(audio_id)
    out = tmp_path / "got.mp3"
    out.write_bytes(got.content)
    info: Any = MP3(out).info
    # CBR; mutagen estimates from the length, a few bit/s off on a two-second file.
    assert abs(info.bitrate - bitrate * 1000) < 100 and info.sample_rate == 44100
    assert 1.5 < info.length < 2.5
    tags = ID3(out)
    assert tags["TIT2"].text == ["Tone"] and tags["TPE1"].text == ["A, B"]
    assert temp_audio(stack.settings) == []  # the work directory is gone


def test_the_transcode_arguments_pin_the_input_and_the_output() -> None:
    src, dest = Path("/w/source.m4a"), Path("/w/out.mp3")
    argv = mp3_argv("ffmpeg", src, dest, "mp4", 192, 1200.0)
    joined = " ".join(argv)
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert "-enable_drefs 0 -f mov -c:a aac -i file:/w/source.m4a" in joined
    assert argv[argv.index("-b:a") + 1] == "192k" and argv[argv.index("-ar") + 1] == "44100"
    assert argv[argv.index("-codec:a") + 1] == "libmp3lame"
    assert argv[argv.index("-map") + 1] == "0:a:0" and "-map_metadata -1" in joined
    assert argv[argv.index("-t") + 1] == "1200.0" and argv[-1] == "file:/w/out.mp3"
    assert "-f mp3 -c:a mp3 -i" in " ".join(mp3_argv("ffmpeg", src, dest, "mp3", 128, 60.0))
    with pytest.raises(Exception):
        mp3_argv("ffmpeg", src, dest, "wav", 128, 60.0)


@needs_ffmpeg
def test_a_container_that_disagrees_with_its_bytes_fails(tmp_path) -> None:
    stack = AudioStack(tmp_path)
    stack.real_transcode = True
    stack.world.audio = real_mp3(tmp_path / "t.mp3").read_bytes()[:200] + b"\x00" * 50
    job = stack.poll(stack.request())
    assert job["status"] == "failed" and job["outcome"] == "processing_failed"
    assert stack.kept_files() == []


def test_the_same_video_and_bitrate_reuse_a_kept_file(stack: AudioStack) -> None:
    first = stack.audio_id(stack.request(key="a"))
    stack.clock.now += 3600
    second = stack.audio_id(stack.request(key="b"))
    assert second == first and stack.world.downloads == [VIDEO]
    other = stack.audio_id(stack.request({"source_ref": REF, "bitrate": 128}, key="c"))
    assert other != first and len(stack.world.downloads) == 2
    # The reuse renewed the file: a day after the first job it is still kept.
    stack.clock.now += stack.settings.audio_ttl_s - 60
    audio_files.purge_expired_audio(stack.settings, stack.store, stack.clock.now)
    assert stack.fetch(first).status_code == 200


def test_an_expired_file_is_swept_and_then_404(stack: AudioStack) -> None:
    audio_id = stack.audio_id(stack.request())
    assert stack.fetch(audio_id).status_code == 200
    stack.clock.now += stack.settings.audio_ttl_s + 1
    assert stack.fetch(audio_id).status_code == 404  # never served once expired
    failed = sweep.sweep(stack.settings, stack.store, stack.clock.now)
    assert failed == [] and stack.kept_files() == []
    assert stack.store.audio_ids() == set()
    # An expired file is not reused either: the next job downloads again.
    stack.audio_id(stack.request(key="again"))
    assert len(stack.world.downloads) == 2


def test_a_running_jobs_file_is_never_deleted(tmp_path) -> None:
    stack = AudioStack(tmp_path, audio_ttl_s=600)
    kept = stack.audio_id(stack.request())
    # A second job is running: it has made its mp3 in its work directory.
    stack.dispatch.then = None
    job_id = stack.request({"source_ref": REF, "bitrate": 128}, key="run").json()["job_id"]
    assert stack.store.start_job(job_id)
    work = stack.settings.work_dir / job_id
    work.mkdir(parents=True)
    (work / "out.mp3").write_bytes(b"in progress")
    # Past the first file's lifetime, inside the running job's lease (dispatch.job_lease_s).
    stack.clock.now += 650
    assert sweep.sweep(stack.settings, stack.store, stack.clock.now) == []
    assert (work / "out.mp3").is_file() and stack.store.running_job_ids() == {job_id}
    assert stack.fetch(kept).status_code == 404 and stack.kept_files() == []


def test_an_orphan_file_is_removed_only_after_a_lease(stack: AudioStack) -> None:
    stack.settings.audio_dir.mkdir(parents=True, exist_ok=True)
    orphan = stack.settings.audio_dir / ("au_" + "1" * 32 + ".mp3")
    orphan.write_bytes(b"x")
    audio_files.purge_expired_audio(stack.settings, stack.store, stack.clock.now)
    assert orphan.is_file()  # it may be a file a job is recording right now
    audio_files.purge_expired_audio(stack.settings, stack.store, stack.clock.now + 86400)
    assert not orphan.exists()


def test_a_download_over_the_size_cap_keeps_and_serves_nothing(tmp_path) -> None:
    stack = AudioStack(tmp_path, max_upload_bytes=1024)
    stack.world.audio = M4A + b"\x00" * 4096
    res = stack.request()
    job = stack.poll(res)
    assert job["status"] == "failed" and job["outcome"] == "processing_failed"
    assert job["failure_kind"] == "out_of_memory" and "audio_id" not in job
    assert stack.transcodes == [] and stack.kept_files() == [] and temp_audio(stack.settings) == []
    assert stack.store.audio_ids() == set()


def test_metadata_over_the_cap_fails_before_any_byte(tmp_path) -> None:
    stack = AudioStack(tmp_path, max_upload_bytes=1024)
    stack.world.infos[VIDEO] = {"id": VIDEO, "duration": 12.0, "filesize": 4096}
    job = stack.poll(stack.request())
    assert job["failure_kind"] == "out_of_memory" and stack.world.downloads == []


def test_sourcing_switched_off_after_acceptance_fails_the_job(stack: AudioStack) -> None:
    stack.dispatch.then = None
    first = stack.request(key="a")
    stack._run(first.json()["job_id"])
    second = stack.request(key="b")
    stack.settings.allow_ytdlp_sourcing = False
    stack._run(second.json()["job_id"])
    job = stack.poll(second)
    assert job["status"] == "failed" and job["outcome"] == "source_unavailable"
    assert stack.world.downloads == [VIDEO]  # no reuse and no download once it is off


def test_a_job_that_is_not_queued_is_not_run_again(stack: AudioStack) -> None:
    res = stack.request()
    stack.audio_id(res)
    execute_audio_job(
        res.json()["job_id"],
        stack.settings,
        stack.store,
        stack.world.client(),
        limits=limits_from(stack.settings),
    )
    assert stack.world.downloads == [VIDEO]


def test_a_provider_failure_is_reported_like_an_analysis_download(stack: AudioStack) -> None:
    stack.world.download_error = "ERROR: [youtube] x: Video unavailable"
    job = stack.poll(stack.request())
    assert job["status"] == "failed" and job["outcome"] == "source_unavailable"
    assert stack.kept_files() == []
