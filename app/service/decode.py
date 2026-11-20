"""Decodes one checked file in the analysis sandbox, with the decoder pinned to its container.

No decoder ever guesses the format: wav and mp3 are read by libsndfile, which must report the same
container and stream the checks found; mp4 by ffmpeg with the ``mov`` demuxer forced, local files
only and no data references. Both decode at most the cap plus a second (the samples librosa would
load). The result equals ``librosa.load(path, sr, mono=True, duration=cap + 1)``, which this
replaces because librosa falls back to a format-guessing decoder (audioread) whenever libsndfile
cannot read a file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import numpy as np

from .container_policy import Container, InvalidMedia, stream_wav_header

_SF_FORMATS = {"wav": ("WAV", "WAVEX"), "mp3": ("MP3",)}
_PIPE_HEADER_MAX = 4096
_EXTRA_S = 1.0


class DecodeTimeout(Exception):
    """The decode ran past its deadline."""


def _soundfile(path: Path, container: Container, cap_s: float) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        if f.format not in _SF_FORMATS[container.media]:
            raise InvalidMedia("decoder disagrees with the container")
        if (f.samplerate, f.channels) != (container.sample_rate_hz, container.channels):
            raise InvalidMedia("decoder disagrees with the container")
        # One read, as librosa does: libsndfile's MPEG reader returns other samples when the
        # same file is read in blocks. The decode deadline is the sandbox child's alarm.
        frames = int((cap_s + _EXTRA_S) * f.samplerate)
        return f.read(frames=frames, dtype="float32", always_2d=False).T


def ffmpeg_argv(ffmpeg: str, path: Path, cap_s: float) -> List[str]:
    """Input options pin the demuxer (``-f mov``), the protocol (local file) and data references
    (off); the output is at most ``cap_s + 1`` s of the first audio stream as 16-bit WAV."""
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-enable_drefs",
        "0",
        "-f",
        "mov",
        "-i",
        "file:" + str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-t",
        repr(float(cap_s + _EXTRA_S)),
        "-acodec",
        "pcm_s16le",
        "-f",
        "wav",
        "pipe:1",
    ]


def pcm_from_wav_stream(out: bytes) -> Tuple[np.ndarray, int]:
    """The samples (librosa's int16 scaling, channels first) and rate of a decoder's WAV output;
    its rate and channels pass the same sanity check as a file's (``stream_wav_header``)."""
    rate, channels, bits, at = stream_wav_header(out[:_PIPE_HEADER_MAX])
    if bits != 16:
        raise InvalidMedia("unexpected decoder output")
    data = out[at:]
    data = data[: len(data) - len(data) % (2 * channels)]
    y = np.frombuffer(data, dtype="<i2").astype(np.float32) * np.float32(1.0 / 32768)
    if channels > 1:
        y = y.reshape((-1, channels)).T
    return y, rate


def _ffmpeg(path: Path, cap_s: float, timeout_s: float) -> Tuple[np.ndarray, int]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise InvalidMedia("no mp4 decoder")
    try:
        done = subprocess.run(
            ffmpeg_argv(ffmpeg, path, cap_s),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(timeout_s, 0.001),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise DecodeTimeout() from None
    if done.returncode != 0:
        raise InvalidMedia("the decoder failed")
    return pcm_from_wav_stream(done.stdout)


def decode(
    path: Path, container: Container, sr: int, cap_s: float, decode_s: float
) -> Tuple[np.ndarray, int]:
    """Mono samples at ``sr``, as ``librosa.load`` returns them; DecodeTimeout or InvalidMedia.
    ``decode_s`` bounds the ffmpeg process; the caller's alarm bounds the whole decode."""
    import librosa

    if container.media == "mp4":
        y, native = _ffmpeg(path, cap_s, decode_s)
    else:
        y, native = _soundfile(path, container, cap_s), container.sample_rate_hz
    if y.size == 0:
        return np.zeros(0, dtype=np.float32), sr
    y = librosa.to_mono(y)
    return librosa.resample(y, orig_sr=native, target_sr=sr, res_type="soxr_hq"), sr
