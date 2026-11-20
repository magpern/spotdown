"""Which audio containers the service accepts: mp3, mp4 and wav, recognised by their bytes.

Pure: it looks only at the first bytes; the file name and Content-Type are never trusted.
"""

from __future__ import annotations

from typing import Optional

SNIFF_BYTES = 16


def sniff(head: bytes) -> Optional[str]:
    """``"mp3"``, ``"mp4"`` or ``"wav"`` for a recognised header, else None."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    if head[:3] == b"ID3":
        return "mp3"
    # An MPEG audio frame header: 11 sync bits, then MPEG version != reserved and layer III.
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        version = (head[1] >> 3) & 0x3
        layer = (head[1] >> 1) & 0x3
        if version != 0x1 and layer == 0x1:
            return "mp3"
    return None
