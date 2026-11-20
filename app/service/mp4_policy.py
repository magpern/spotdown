"""Whether a file is a well-formed MP4/M4A whose audio is AAC (pure; see ``container_policy``).

Every top-level box must fit in the file and be of a known kind, so nothing else hides behind the
``ftyp``; ``ftyp`` comes first and its major brand is an MP4 one (no QuickTime, no 3GP). The
``moov`` box is walked through every ``trak/mdia/minf/stbl``: every track is a sound track carrying
only AAC or a video track (``mp4_entry_policy``), and there is at least one sound track. Every
sample table (and every fragment's ``trun``) must hold the entries it declares, so the decoder
never sizes an index by a count the file merely claims. Tag boxes (``udta``, ``meta``) are skipped
unread here (the probe's stream-info reader walks their headers, not their values).
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .container_policy import Container, InvalidMedia, Read, exact
from .mp4_boxes import Box, boxes, child, need
from .mp4_entry_policy import check_entries

_MAX_BOXES = 8192
_MAX_MOOV = 32 * 1024 * 1024
_BRANDS = {
    b"M4A ",
    b"M4B ",
    b"mp41",
    b"mp42",
    b"isom",
    b"iso2",
    b"iso3",
    b"iso4",
    b"iso5",
    b"iso6",
    b"dash",
    b"avc1",
}
_TOP = {
    b"ftyp",
    b"styp",
    b"moov",
    b"mdat",
    b"free",
    b"skip",
    b"wide",
    b"uuid",
    b"udta",
    b"meta",
    b"pdin",
    b"sidx",
    b"ssix",
    b"moof",
    b"mfra",
    b"emsg",
    b"prft",
}
# Full-box tables (version and flags, then an entry count): bytes per entry.
_TABLES = {b"stts": 8, b"ctts": 8, b"stsc": 12, b"stss": 4, b"stco": 4, b"co64": 8}


def _check_table(read: Read, box: Box) -> None:
    kind, at, end = box
    if kind == b"stsz":
        sample_size, count = struct.unpack(">II", exact(read, at + 4, 8))
        need = 12 + (4 * count if sample_size == 0 else 0)
        if need > end - at or count > end:  # a fixed-size table still names one sample per byte
            raise InvalidMedia("sample table larger than its box")
        return
    per = _TABLES[kind]
    count = struct.unpack(">I", exact(read, at + 4, 4))[0]
    if 8 + per * count > end - at:
        raise InvalidMedia("sample table larger than its box")


def _check_trun(read: Read, box: Box, size: int) -> None:
    flags, count = struct.unpack(">II", exact(read, box[1], 8))
    fixed = 8 + 4 * bool(flags & 0x1) + 4 * bool(flags & 0x4)
    per = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
    if fixed + per * count > box[2] - box[1] or count > size:
        raise InvalidMedia("fragment table larger than its box")


def _track(read: Read, trak: Box, budget: List[int]) -> Optional[Tuple[int, int, Optional[float]]]:
    """``(rate, channels, duration)`` of a sound track (None for a video track), after checking
    every sample table and every sample entry of the track (``mp4_entry_policy``)."""
    mdia = need(child(read, trak, b"mdia", budget))
    stbl = need(child(read, need(child(read, mdia, b"minf", budget)), b"stbl", budget))
    hdlr = need(child(read, mdia, b"hdlr", budget))
    mdhd = need(child(read, mdia, b"mdhd", budget))
    stsd: Optional[Box] = None
    for box in boxes(read, stbl[1], stbl[2], budget):
        if box[0] == b"stsd":
            stsd = box
        elif box[0] in _TABLES or box[0] == b"stsz":
            _check_table(read, box)
    stream = check_entries(read, need(stsd), exact(read, hdlr[1] + 8, 4), budget)
    if stream is None:  # a video track (never decoded: only the first audio stream is mapped)
        return None
    rate, channels = stream
    version = exact(read, mdhd[1], 1)[0]
    if version == 1:
        scale, duration = struct.unpack(">IQ", exact(read, mdhd[1] + 20, 12))
    else:
        scale, duration = struct.unpack(">II", exact(read, mdhd[1] + 12, 8))
    if scale == 0:
        raise InvalidMedia("bad media header")
    unknown = duration in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)  # fragmented, or not written
    return rate, channels, None if unknown else duration / float(scale)


def _check_moof(read: Read, moof: Box, size: int, budget: List[int]) -> None:
    for traf in boxes(read, moof[1], moof[2], budget):
        if traf[0] == b"traf":
            for box in boxes(read, traf[1], traf[2], budget):
                if box[0] == b"trun":
                    _check_trun(read, box, size)


def inspect_mp4(read: Read, size: int) -> Container:
    budget = [_MAX_BOXES]
    top = list(boxes(read, 0, size, budget))
    kinds = [b[0] for b in top]
    if not top or kinds[0] != b"ftyp" or kinds.count(b"ftyp") != 1:
        raise InvalidMedia("not an mp4 file")
    if any(k not in _TOP for k in kinds):
        raise InvalidMedia("unknown box")
    if kinds.count(b"moov") != 1 or b"mdat" not in kinds:
        raise InvalidMedia("incomplete mp4 file")
    ftyp = top[0]
    # The major brand decides: a QuickTime or 3GP file that lists an MP4 brand as compatible is
    # still refused.
    if exact(read, ftyp[1], 4) not in _BRANDS:
        raise InvalidMedia("not an mp4 brand")
    moov = top[kinds.index(b"moov")]
    if moov[2] - moov[1] > _MAX_MOOV:
        raise InvalidMedia("oversized movie header")
    sound: Optional[Tuple[int, int, Optional[float]]] = None
    for box in boxes(read, moov[1], moov[2], budget):
        if box[0] == b"trak":
            track = _track(read, box, budget)
            sound = sound or track
    if sound is None:
        raise InvalidMedia("no sound track")
    for box in top:
        if box[0] == b"moof":
            _check_moof(read, box, size, budget)
    return Container("mp4", sound[0], sound[1], sound[2])
