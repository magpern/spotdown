"""The analysis subprocess: decode one file and run the core analyser on it.

Started by ``sandbox.run_analysis`` with rlimits already applied (memory, CPU time, file size),
no inherited environment beyond a short allowlist and no network use of its own. It reads one
file, writes one JSON result, and reports failures by exit code only. The file's structure is
checked (``container_io``) before it is decoded with a decoder pinned to that container
(``decode``); the checks and the decode run under a wall-time alarm.

Usage: ``python -m app.service.sandbox_child <audio> <result.json> <max_decoded_s> <media>
<decode_s>``
"""

from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

from . import container_io, decode
from .container_policy import Container, InvalidMedia, too_long

EXIT_OK = 0
EXIT_DECODE = 10
EXIT_TOO_LONG = 11
EXIT_MEMORY = 12
EXIT_ANALYSIS = 13
EXIT_INVALID_MEDIA = 14
EXIT_DECODE_TIMEOUT = 15
EXIT_USAGE = 2

_CODEC = {"wav": "pcm", "mp3": "mp3", "mp4": "aac"}


def _probe(path: Path, container: Container) -> Dict[str, Any]:
    """Stream facts for provenance: the checked container's, plus mutagen's stream info for the
    bitrate and the mp4 codec. Only stream-info readers are used: no tag is ever parsed."""
    probe: Dict[str, Any] = {
        "codec": _CODEC[container.media],
        "sample_rate_hz": container.sample_rate_hz,
        "channels": container.channels,
    }
    if container.media == "wav":
        bits = container.bits_per_sample or 0
        probe["bitrate_bps"] = container.sample_rate_hz * container.channels * bits
        return probe
    with path.open("rb") as fh:
        if container.media == "mp3":
            from mutagen.mp3 import MPEGInfo

            info: Any = MPEGInfo(fh, container.audio_offset)
        else:
            from mutagen.mp4 import Atoms, MP4Info  # pyright: ignore[reportPrivateImportUsage]

            info = MP4Info()
            info.load(Atoms(fh), fh)
    probe["bitrate_bps"] = int(getattr(info, "bitrate", 0) or 0)
    codec = getattr(info, "codec", None)
    if container.media == "mp4" and isinstance(codec, str) and codec:
        probe["codec"] = codec
    return probe


def _components() -> Dict[str, str]:
    from importlib.metadata import version

    out = {"python": sys.version.split()[0]}
    for name in ("librosa", "numpy", "scipy", "soundfile", "mutagen"):
        try:
            out[name] = version(name)
        except Exception:  # noqa: BLE001
            pass
    return out


def run(audio: Path, result: Path, max_decoded_s: float, media: str, decode_s: float) -> int:
    timings: Dict[str, float] = {}
    t0 = time.monotonic()
    # The decode deadline: SIGALRM with its default action ends this process even inside a C
    # decoder; the parent reads the signal as a timeout.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, decode_s)
    try:
        # The whole structure is checked before any decoder sees a byte (every source).
        container = container_io.inspect_file(audio, media, max_decoded_s)
        if too_long(container, max_decoded_s):
            return EXIT_TOO_LONG
        probe = _probe(audio, container)
        from ..core import SR

        # Decode at most a second past the cap, so an overlong file is refused without ever
        # holding more than the cap in memory.
        y, sr = decode.decode(audio, container, SR, max_decoded_s, decode_s)
    except InvalidMedia:
        return EXIT_INVALID_MEDIA
    except decode.DecodeTimeout:
        return EXIT_DECODE_TIMEOUT
    except MemoryError:
        return EXIT_MEMORY
    except Exception:  # noqa: BLE001
        return EXIT_DECODE
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if y.size == 0:
        return EXIT_DECODE
    if y.size / float(sr) > max_decoded_s:
        return EXIT_TOO_LONG
    timings["decode"] = time.monotonic() - t0

    t1 = time.monotonic()
    try:
        from ..core.analyser import analyse_signal

        core = analyse_signal(y, int(sr))
    except MemoryError:
        return EXIT_MEMORY
    except Exception:  # noqa: BLE001
        return EXIT_ANALYSIS
    timings["analyse"] = time.monotonic() - t1

    payload = {"core": core, "probe": probe, "components": _components(), "timings_s": timings}
    result.write_text(json.dumps(payload), encoding="utf-8")
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 5 or not float(argv[4]) > 0:  # a zero timer would disarm the deadline
        return EXIT_USAGE
    try:
        return run(Path(argv[0]), Path(argv[1]), float(argv[2]), argv[3], float(argv[4]))
    except MemoryError:
        return EXIT_MEMORY


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
