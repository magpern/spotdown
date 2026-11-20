"""The analysis subprocess: decode one file and run the core analyser on it.

Started by ``sandbox.run_analysis`` with rlimits already applied (memory, CPU time, file size),
no inherited environment beyond a short allowlist and no network use of its own. It reads one
file, writes one JSON result, and reports failures by exit code only.

Usage: ``python -m app.service.sandbox_child <audio> <result.json> <max_decoded_s> <media>``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

EXIT_OK = 0
EXIT_DECODE = 10
EXIT_TOO_LONG = 11
EXIT_MEMORY = 12
EXIT_ANALYSIS = 13
EXIT_USAGE = 2

_CODEC = {"wav": "pcm", "mp3": "mp3", "mp4": "aac"}


def _probe(path: Path, media: str) -> Dict[str, Any]:
    """Container facts for provenance (mutagen, pure Python); soundfile as a fallback."""
    from mutagen import File as read_tags  # pyright: ignore[reportPrivateImportUsage]

    probe: Dict[str, Any] = {"codec": _CODEC.get(media, media)}
    info = getattr(read_tags(str(path)), "info", None)
    if info is not None:
        probe["sample_rate_hz"] = int(getattr(info, "sample_rate", 0) or 0)
        probe["channels"] = int(getattr(info, "channels", 0) or 0)
        probe["bitrate_bps"] = int(getattr(info, "bitrate", 0) or 0)
        codec = getattr(info, "codec", None)
        if media == "mp4" and isinstance(codec, str) and codec:
            probe["codec"] = codec
    if not probe.get("sample_rate_hz") or not probe.get("channels"):
        import soundfile

        sf_info = soundfile.info(str(path))
        probe["sample_rate_hz"] = int(sf_info.samplerate)
        probe["channels"] = int(sf_info.channels)
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


def run(audio: Path, result: Path, max_decoded_s: float, media: str) -> int:
    timings: Dict[str, float] = {}
    t0 = time.monotonic()
    try:
        probe = _probe(audio, media)
        import librosa

        from ..core import SR

        # Decode at most a second past the cap, so an overlong file is refused without ever
        # holding more than the cap in memory.
        y, sr = librosa.load(str(audio), sr=SR, mono=True, duration=max_decoded_s + 1.0)
    except MemoryError:
        return EXIT_MEMORY
    except Exception:  # noqa: BLE001
        return EXIT_DECODE
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
    if len(argv) != 4:
        return EXIT_USAGE
    try:
        return run(Path(argv[0]), Path(argv[1]), float(argv[2]), argv[3])
    except MemoryError:
        return EXIT_MEMORY


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
