"""The ffmpeg command line of the service's mp3 conversion (``POST /v2/audio``). Pure: it builds
an argument list and runs nothing (``transcode_child`` runs it in the sandbox)."""

from __future__ import annotations

from pathlib import Path
from typing import List

# The input side of ``mp3_argv``: demuxer and decoder pinned per checked container.
_PINNED_INPUT = {
    "mp4": ["-enable_drefs", "0", "-f", "mov", "-c:a", "aac"],
    "mp3": ["-f", "mp3", "-c:a", "mp3"],
}


# MPEG-1 layer III, so every bitrate up to 320 kbps exists (at 22.05 kHz LAME stops at 160).
MP3_SAMPLE_RATE_HZ = 44100


class TranscodeError(RuntimeError):
    pass


def mp3_argv(
    ffmpeg: str, src: Path, dest: Path, media: str, bitrate: int, cap_s: float
) -> List[str]:
    """The service's mp3 conversion of untrusted audio (run in the sandbox, ``transcode_child``).

    Nothing is guessed from the bytes: the demuxer and decoder are pinned to the container the
    checks found (``mov`` with ``aac``, or ``mp3``; another codec fails), local files only, no data
    references, the first audio stream only, no metadata carried over, at most ``cap_s`` seconds,
    CBR mp3 at 44.1 kHz and ``bitrate`` kbps without ID3v1. TranscodeError for another container."""
    if media not in _PINNED_INPUT:
        raise TranscodeError("unsupported container")
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
        *_PINNED_INPUT[media],
        "-i",
        "file:" + str(src),
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
        repr(float(cap_s)),
        "-ar",
        str(MP3_SAMPLE_RATE_HZ),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        f"{int(bitrate)}k",
        "-write_id3v1",
        "0",
        "-f",
        "mp3",
        "-y",
        "file:" + str(dest),
    ]
