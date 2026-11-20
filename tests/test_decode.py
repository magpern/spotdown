"""The sandbox decoder: pinned to the checked container, capped, librosa-exact (the decode
deadline of the child itself is tested through the real sandbox, test_sandbox_hostile.py)."""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

import librosa
import numpy as np
import pytest
import soundfile as sf

from app.core import SR
from app.service import container_io, decode
from app.service.container_policy import Container, InvalidMedia

from . import media_fixtures as mf

needs_ffmpeg = pytest.mark.skipif(not mf.has_ffmpeg(), reason="ffmpeg is not installed")


def soon(seconds: float) -> float:
    return time.monotonic() + seconds


def write_wav(path: Path, seconds: float, rate: int = 22050, channels: int = 1) -> Path:
    sf.write(str(path), mf.tone(seconds, rate, channels), rate, subtype="PCM_16")
    return path


def checked(path: Path, media: str) -> Container:
    return container_io.inspect_file(path, media, 1200.0)


def librosa_load(path: Path, cap: float) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return librosa.load(str(path), sr=SR, mono=True, duration=cap + 1.0)[0]


@pytest.mark.parametrize("kind", ["wav_mono", "wav_stereo", "mp3"])
def test_decoding_equals_librosa_load(tmp_path: Path, kind: str) -> None:
    if kind == "mp3":
        path, media = mf.real_mp3(tmp_path / "a.mp3"), "mp3"
    else:
        path = write_wav(tmp_path / "a.wav", 3.0, 44100, 2 if kind == "wav_stereo" else 1)
        media = "wav"
    y, sr = decode.decode(path, checked(path, media), SR, 1200.0, soon(60.0))
    assert sr == SR and np.array_equal(y, librosa_load(path, 1200.0))


@needs_ffmpeg
def test_mp4_decoding_equals_librosas_ffmpeg_path(tmp_path: Path) -> None:
    path = mf.real_m4a(tmp_path / "a.m4a")
    y, _ = decode.decode(path, checked(path, "mp4"), SR, 1200.0, soon(60.0))
    assert y.size > 0 and np.array_equal(y, librosa_load(path, 1200.0))


def test_at_most_the_cap_plus_a_second_is_decoded(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "a.wav", 10.0)
    y, _ = decode.decode(path, checked(path, "wav"), SR, 3.0, soon(60.0))
    assert y.size == 4 * SR


@pytest.mark.parametrize(
    "claimed",
    [
        Container("mp3", 22050, 1, 2.0),  # libsndfile reads a WAV: not the sniffed container
        Container("wav", 44100, 1, 2.0),  # another sample rate
        Container("wav", 22050, 2, 2.0),  # another channel count
    ],
)
def test_the_decoder_must_agree_with_the_container(tmp_path: Path, claimed: Container) -> None:
    path = write_wav(tmp_path / "a.wav", 2.0)
    with pytest.raises(InvalidMedia):
        decode.decode(path, claimed, SR, 1200.0, soon(60.0))


def test_ffmpeg_is_pinned_to_one_local_mp4_file_and_the_cap() -> None:
    argv = decode.ffmpeg_argv("/usr/bin/ffmpeg", Path("/w/up_1"), 1200.0)
    pairs = dict(zip(argv[:-1], argv[1:]))
    assert argv.index("-f") < argv.index("-i") and argv[argv.index("-f") + 1] == "mov"
    assert pairs["-protocol_whitelist"] == "file" and pairs["-enable_drefs"] == "0"
    assert pairs["-c:a"] == "aac" and argv.index("-c:a") < argv.index("-i")  # decoder forced
    assert argv.index("-protocol_whitelist") < argv.index("-i")
    assert pairs["-i"] == "file:/w/up_1" and pairs["-map"] == "0:a:0"
    assert float(pairs["-t"]) == 1201.0 and argv.index("-t") > argv.index("-i")
    assert argv[-1] == "pipe:1" and "-nostdin" in argv


def _fake_ffmpeg(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "ffmpeg"
    script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    os.chmod(script, 0o755)
    monkeypatch.setattr(decode.shutil, "which", lambda name: str(script))


def test_the_ffmpeg_decode_stops_before_the_childs_alarm(tmp_path, monkeypatch) -> None:
    """ffmpeg gets the time left minus a margin, so its own timeout (DecodeTimeout) fires before
    the alarm that would end the whole child."""
    _fake_ffmpeg(tmp_path, "sleep 5", monkeypatch)
    t0 = time.monotonic()
    with pytest.raises(decode.DecodeTimeout):
        decode.decode(tmp_path / "x", Container("mp4", 44100, 2, None), SR, 1200.0, soon(0.9))
    assert time.monotonic() - t0 < 0.9 - decode.FFMPEG_MARGIN_S / 2
    with pytest.raises(decode.DecodeTimeout):  # no time left at all
        decode.decode(tmp_path / "x", Container("mp4", 44100, 2, None), SR, 1200.0, soon(0.1))


@pytest.mark.parametrize("body", ["exit 1", "exit 0", "printf 'RIFF....WAVEjunk'"])
def test_a_failed_or_empty_ffmpeg_decode_is_invalid_media(tmp_path, monkeypatch, body) -> None:
    _fake_ffmpeg(tmp_path, body, monkeypatch)
    with pytest.raises(InvalidMedia):
        decode.decode(tmp_path / "x", Container("mp4", 44100, 2, None), SR, 1200.0, soon(10.0))


def test_no_ffmpeg_is_invalid_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(decode.shutil, "which", lambda name: None)
    with pytest.raises(InvalidMedia):
        decode.decode(tmp_path / "x", Container("mp4", 44100, 2, None), SR, 1200.0, soon(10.0))


@needs_ffmpeg
def test_ffmpeg_refuses_another_container_behind_an_mp4_claim(tmp_path: Path) -> None:
    """Without ``-f mov`` ffmpeg would probe the bytes and happily decode the WAV."""
    path = write_wav(tmp_path / "a.wav", 2.0)
    with pytest.raises(InvalidMedia):
        decode.decode(path, Container("mp4", 22050, 1, None), SR, 1200.0, soon(30.0))


def test_the_decoders_wav_output_is_checked_and_scaled_like_librosa() -> None:
    pcm = np.array([[0, -32768], [16384, 32767]], dtype="<i2")
    head = mf.wav(rate=44100, channels=2, seconds=0, data_size=0xFFFFFFFF, riff_size=0)
    y, rate = decode.pcm_from_wav_stream(head + pcm.tobytes() + b"\x01")
    assert rate == 44100 and y.shape == (2, 2)
    assert np.array_equal(y, librosa.util.buf_to_float(pcm.reshape(-1), n_bytes=2).reshape(-1, 2).T)
    for bad in (
        mf.wav(bits=24, seconds=0, data_size=0xFFFFFFFF),
        mf.wav(rate=4000, seconds=0, data_size=0xFFFFFFFF),
        b"not a wav stream",
    ):
        with pytest.raises(InvalidMedia):
            decode.pcm_from_wav_stream(bad)


@needs_ffmpeg
def test_ffmpeg_refuses_mp3_inside_an_mp4_even_past_the_check(tmp_path: Path) -> None:
    """Without ``-c:a aac`` ffmpeg would pick the mp3 decoder for an mp4a entry holding mp3."""
    import subprocess

    src = write_wav(tmp_path / "a.wav", 1.0)
    out = tmp_path / "mp3.mp4"
    argv = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
    subprocess.run([*argv, "-c:a", "libmp3lame", "-f", "mp4", str(out)], check=True)
    with pytest.raises(InvalidMedia):
        decode.decode(out, Container("mp4", 22050, 1, None), SR, 1200.0, soon(30.0))
