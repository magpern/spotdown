"""The transcode subprocess: check one downloaded file's structure, then convert it to mp3.

Started by ``audio_transcode.run_transcode`` through the analysis sandbox (``sandbox.run_child``:
rlimits on memory, CPU time and file size, a short environment allowlist, a wall deadline). The
worker never runs ffmpeg on untrusted bytes itself. The container is checked (``container_io``)
before ffmpeg sees a byte, and ffmpeg is pinned to that container (``app.transcode.mp3_argv``); the
check and the conversion run under a wall-time alarm. Failures are exit codes only, the same ones
as ``sandbox_child``, so ``sandbox`` maps them to the same failure reasons.

Usage: ``python -m app.service.transcode_child <src> <dest> <media> <bitrate> <max_decoded_s>
<deadline_s>``
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..transcode import TranscodeError, mp3_argv
from . import audio_policy, container_io
from .container_policy import InvalidMedia, too_long
from .sandbox_child import (
    EXIT_DECODE,
    EXIT_DECODE_TIMEOUT,
    EXIT_INVALID_MEDIA,
    EXIT_MEMORY,
    EXIT_OK,
    EXIT_TOO_LONG,
    EXIT_USAGE,
)

# ffmpeg is stopped this long before the alarm would end the whole child.
FFMPEG_MARGIN_S = 0.5


def run(src: Path, dest: Path, media: str, bitrate: int, max_s: float, deadline_s: float) -> int:
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, deadline_s)
    deadline = time.monotonic() + deadline_s
    try:
        container = container_io.inspect_file(src, media, max_s)
        if too_long(container, max_s):
            return EXIT_TOO_LONG
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return EXIT_DECODE
        argv = mp3_argv(ffmpeg, src, dest, media, bitrate, max_s)
        done = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(deadline - time.monotonic() - FFMPEG_MARGIN_S, 0.001),
            check=False,
        )
    except (InvalidMedia, TranscodeError):
        return EXIT_INVALID_MEDIA
    except subprocess.TimeoutExpired:
        return EXIT_DECODE_TIMEOUT
    except MemoryError:
        return EXIT_MEMORY
    except Exception:  # noqa: BLE001
        return EXIT_DECODE
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if done.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        return EXIT_DECODE
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        return EXIT_USAGE
    try:
        bitrate, max_s, deadline_s = int(argv[3]), float(argv[4]), float(argv[5])
    except ValueError:
        return EXIT_USAGE
    if bitrate not in audio_policy.BITRATES or not deadline_s > 0 or not max_s > 0:
        return EXIT_USAGE  # a zero timer would disarm the deadline
    try:
        return run(Path(argv[0]), Path(argv[1]), argv[2], bitrate, max_s, deadline_s)
    except MemoryError:
        return EXIT_MEMORY


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
