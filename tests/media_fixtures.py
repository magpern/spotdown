"""Audio containers built byte by byte in the tests: no real music, nothing downloaded.

``wav``, ``mp3_frames`` and ``mp4`` build structurally valid files whose every field a test can
spoil; ``real_mp3`` and ``real_m4a`` encode a generated signal for the decode tests.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

MARKER = "TAG-MARKER-7f3c"  # put in tags and names; must never reach an output


def wav(
    rate: int = 8000,
    channels: int = 1,
    bits: int = 16,
    seconds: float = 0.5,
    tag: int = 1,
    chunks: Optional[List[bytes]] = None,
    data_size: Optional[int] = None,
    riff_size: Optional[int] = None,
) -> bytes:
    align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)
    data = b"\x00" * (int(rate * seconds) * align)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    for extra in chunks or []:
        body += extra
    size = len(data) if data_size is None else data_size
    body += b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body) if riff_size is None else riff_size) + body


def info_chunk(text: str) -> bytes:
    """A LIST/INFO chunk with a title: the tag a WAV file carries."""
    value = text.encode() + b"\x00"
    value += b"\x00" * (len(value) & 1)
    info = b"INFO" + b"INAM" + struct.pack("<I", len(value)) + value
    return b"LIST" + struct.pack("<I", len(info)) + info


def id3v2(text: str = MARKER, declared: Optional[int] = None) -> bytes:
    """An ID3v2.3 tag with one title frame."""
    payload = b"\x00" + text.encode("latin-1")
    frame = b"TIT2" + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
    size = len(frame) if declared is None else declared
    synch = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + synch + frame


def mp3_header(version: int = 3, br: int = 9, sr: int = 0, mode: int = 3) -> bytes:
    """Layer III, no CRC, no padding. version 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5."""
    return bytes([0xFF, 0xE0 | (version << 3) | (0x1 << 1) | 0x1, (br << 4) | (sr << 2), mode << 6])


def mp3_frame_len(version: int = 3, br: int = 9, sr: int = 0) -> int:
    rates = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
    kbps = {
        3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    }
    samples = 1152 if version == 3 else 576
    return samples // 8 * kbps[3 if version == 3 else 2][br] * 1000 // rates[version][sr]


def mp3_frames(n: int = 20, version: int = 3, br: int = 9, sr: int = 0, mode: int = 3) -> bytes:
    frame = mp3_header(version, br, sr, mode)
    frame += b"\x00" * (mp3_frame_len(version, br, sr) - 4)
    return frame * n


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, payload: bytes) -> bytes:
    return box(kind, b"\x00\x00\x00\x00" + payload)


def mp4a(rate: int = 44100, channels: int = 2, kind: bytes = b"mp4a", version: int = 0) -> bytes:
    entry = b"\x00" * 6 + b"\x00\x01" + struct.pack(">HH4s", version, 0, b"\x00" * 4)
    entry += struct.pack(">HHHHI", channels, 16, 0, 0, (rate & 0xFFFF) << 16)
    return box(kind, entry)


def mp4(
    rate: int = 44100,
    channels: int = 2,
    brand: bytes = b"M4A ",
    compat: bytes = b"isom",
    entry: Optional[bytes] = None,
    handler: bytes = b"soun",
    duration: int = 441000,
    stsz: Optional[bytes] = None,
    tail: bytes = b"",
    extra_top: Optional[Dict[str, bytes]] = None,
    moov_count: int = 1,
) -> bytes:
    stsd = full(b"stsd", struct.pack(">I", 1) + (entry or mp4a(rate, channels)))
    tables = full(b"stts", struct.pack(">III", 1, 10, 1024))
    tables += full(b"stsc", struct.pack(">IIII", 1, 1, 10, 1))
    tables += stsz if stsz is not None else full(b"stsz", struct.pack(">II", 0, 10) + b"\x00" * 40)
    tables += full(b"stco", struct.pack(">II", 1, 0))
    stbl = box(b"stbl", stsd + tables)
    minf = box(b"minf", full(b"smhd", b"\x00" * 4) + stbl)
    hdlr = full(b"hdlr", b"\x00" * 4 + handler + b"\x00" * 12 + b"\x00")
    mdhd = full(b"mdhd", struct.pack(">IIIIHH", 0, 0, rate, duration, 0, 0))
    trak = box(b"trak", box(b"mdia", mdhd + hdlr + minf))
    udta = box(b"udta", box(b"\xa9nam", MARKER.encode()))
    moov = box(b"moov", full(b"mvhd", b"\x00" * 96) + trak + udta) * moov_count
    ftyp = box(b"ftyp", brand + b"\x00\x00\x02\x00" + brand + compat)
    tops = b"".join((extra_top or {}).values())
    return ftyp + moov + tops + box(b"mdat", b"\x00" * 64) + tail


def tone(seconds: float, rate: int, channels: int = 1) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    y = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return np.stack([y] * channels, axis=1) if channels > 1 else y


def real_mp3(path: Path, seconds: float = 3.0, rate: int = 22050) -> Path:
    sf.write(str(path), tone(seconds, rate), rate, format="MP3")
    return path


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def real_m4a(path: Path, seconds: float = 3.0, rate: int = 22050) -> Path:
    """An AAC file encoded by ffmpeg (tests using it skip without ffmpeg)."""
    src = path.with_suffix(".src.wav")
    sf.write(str(src), tone(seconds, rate), rate, subtype="PCM_16")
    argv = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-c:a", "aac"]
    subprocess.run([*argv, str(path)], check=True)
    src.unlink()
    return path
