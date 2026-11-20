"""Which sample entries an mp4 track may carry (pure; see ``mp4_policy``).

ffmpeg's mov demuxer decides that a track is audio from its sample entry's code, not from the
track's handler, so every track is checked, whatever its handler: a sound track must carry only
AAC (``mp4a`` whose ``esds`` names MPEG-4 or MPEG-2 AAC, never mp3, ALS or another object type in
an ``mp4a`` wrapper), a video track only a known video entry, and any other track is refused (a
``text`` or ``meta`` track could hold an audio entry). The entries must fill ``stsd`` exactly.
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .container_policy import InvalidMedia, Read, check_stream, exact
from .mp4_boxes import Box, boxes

_MAX_ENTRIES = 16
_MAX_ESDS = 1024
_VIDEO = {b"avc1", b"avc3", b"hvc1", b"hev1", b"mp4v", b"av01", b"vp09"}
# esds objectTypeIndication: MPEG-4 audio, and the three MPEG-2 AAC profiles.
_AAC_OTI = {0x40, 0x66, 0x67, 0x68}
# MPEG-4 audioObjectType: AAC main, LC, SSR, LTP, SBR (HE-AAC), PS (HE-AAC v2).
_AAC_AOT = {1, 2, 3, 4, 5, 29}


def _descriptor(buf: bytes, pos: int, tag: int) -> Tuple[int, int]:
    """``(body start, body end)`` of the descriptor ``tag`` at ``pos`` (expandable length)."""
    if pos >= len(buf) or buf[pos] != tag:
        raise InvalidMedia("bad aac descriptor")
    length, pos = 0, pos + 1
    for _ in range(4):
        if pos >= len(buf):
            raise InvalidMedia("bad aac descriptor")
        byte, pos = buf[pos], pos + 1
        length = (length << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    if pos + length > len(buf):
        raise InvalidMedia("bad aac descriptor")
    return pos, pos + length


def check_esds(buf: bytes) -> None:
    """``buf`` is an ``esds`` payload (after version and flags) naming AAC."""
    at, end = _descriptor(buf, 0, 0x03)  # ES_Descriptor
    if at + 3 > end:
        raise InvalidMedia("bad aac descriptor")
    flags, at = buf[at + 2], at + 3
    at += 2 * bool(flags & 0x80) + 2 * bool(flags & 0x20)
    if flags & 0x40:  # a URL: the stream is elsewhere
        raise InvalidMedia("external aac stream")
    at, end = _descriptor(buf[:end], at, 0x04)  # DecoderConfigDescriptor
    if at >= end or buf[at] not in _AAC_OTI:
        raise InvalidMedia("not aac audio")
    # DecoderSpecificInfo (an AudioSpecificConfig), inside the DecoderConfigDescriptor.
    at, end = _descriptor(buf[:end], at + 13, 0x05)
    if end - at < 2:  # an AudioSpecificConfig has at least an object type and a rate index
        raise InvalidMedia("not aac audio")
    # The five-bit object type; 31 escapes to the types from 32 up, none of which is AAC.
    if buf[at] >> 3 not in _AAC_AOT:
        raise InvalidMedia("not aac audio")


def _esds(read: Read, entry: Box, budget: List[int]) -> bytes:
    version = struct.unpack(">H", exact(read, entry[1] + 8, 2))[0]
    if version not in (0, 1):
        raise InvalidMedia("unsupported sample entry")
    children = entry[1] + (28 if version == 0 else 44)
    found: Optional[Box] = None
    for child in boxes(read, children, entry[2], budget):
        if child[0] == b"wave" and found is None:  # QuickTime keeps esds one level down
            for inner in boxes(read, child[1], child[2], budget):
                found = inner if inner[0] == b"esds" else found
        elif child[0] == b"esds":
            found = child
    if found is None or not 4 < found[2] - found[1] <= _MAX_ESDS:
        raise InvalidMedia("not aac audio")
    return exact(read, found[1] + 4, found[2] - found[1] - 4)


def _aac(read: Read, entry: Box, budget: List[int]) -> Tuple[int, int]:
    if entry[0] != b"mp4a":
        raise InvalidMedia("not an aac track")
    if entry[2] - entry[1] < 28:  # every field read lies inside the entry's own box
        raise InvalidMedia("truncated sample entry")
    head = exact(read, entry[1], 28)
    channels = struct.unpack(">H", head[16:18])[0]
    rate = struct.unpack(">I", head[24:28])[0] >> 16
    check_stream(rate, channels)
    check_esds(_esds(read, entry, budget))
    return rate, channels


def check_entries(
    read: Read, stsd: Box, handler: bytes, budget: List[int]
) -> Optional[Tuple[int, int]]:
    """``(rate, channels)`` of a sound track's first entry; None for a video track; InvalidMedia
    for anything else."""
    count = struct.unpack(">I", exact(read, stsd[1] + 4, 4))[0]
    entries = list(boxes(read, stsd[1] + 8, stsd[2], budget))
    if not 1 <= count <= _MAX_ENTRIES or len(entries) != count:
        raise InvalidMedia("bad sample description")
    if handler == b"vide":
        if any(e[0] not in _VIDEO for e in entries):
            raise InvalidMedia("unsupported video track")
        return None
    if handler != b"soun":
        raise InvalidMedia("unsupported track")
    streams = [_aac(read, e, budget) for e in entries]
    return streams[0]
