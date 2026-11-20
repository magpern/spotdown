# pyright: reportPrivateImportUsage=false
# (mutagen.id3 re-exports its frame classes without __all__; the imports are valid.)
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import httpx

from .schemas import TrackMeta


class TranscodeError(RuntimeError):
    pass


def transcode(src: Path, dest: Path, fmt: str, bitrate: int) -> Path:
    """Transcode any audio/video file to mp3 or m4a at the given bitrate (kbps)."""
    if fmt == "mp3":
        codec_args = ["-codec:a", "libmp3lame", "-b:a", f"{bitrate}k"]
    elif fmt == "m4a":
        codec_args = ["-codec:a", "aac", "-b:a", f"{bitrate}k"]
    else:
        raise TranscodeError(f"Unsupported format: {fmt}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vn",  # drop any video stream
        *codec_args,
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        tail = (proc.stderr or "")[-500:]
        raise TranscodeError(f"ffmpeg failed: {tail}")
    return dest


def _download_cover(url: str) -> Optional[bytes]:
    try:
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception:  # noqa: BLE001
        return None


def tag(path: Path, fmt: str, meta: TrackMeta) -> None:
    """Write basic tags + embed cover art. Best-effort; never fatal."""
    try:
        cover = _download_cover(meta.cover_url) if meta.cover_url else None
        if fmt == "mp3":
            from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TSRC

            try:
                audio = ID3(path)
            except Exception:  # noqa: BLE001
                audio = ID3()
            audio.setall("TIT2", [TIT2(encoding=3, text=meta.title)])
            if meta.artist:
                audio.setall("TPE1", [TPE1(encoding=3, text=meta.artist)])
            if meta.album:
                audio.setall("TALB", [TALB(encoding=3, text=meta.album)])
            if meta.isrc:
                audio.setall("TSRC", [TSRC(encoding=3, text=meta.isrc)])
            if cover:
                audio.setall(
                    "APIC",
                    [APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover)],
                )
            audio.save(path)
        elif fmt == "m4a":
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(path)
            audio["\xa9nam"] = meta.title
            if meta.artist:
                audio["\xa9ART"] = meta.artist
            if meta.album:
                audio["\xa9alb"] = meta.album
            if cover:
                audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
    except Exception:  # noqa: BLE001
        # Tagging problems should not fail an otherwise-good conversion.
        pass
