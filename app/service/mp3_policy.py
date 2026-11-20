"""Whether a file is a well-formed MPEG audio (Layer III) stream (pure; see ``container_policy``).

An optional ID3v2 tag is skipped by its declared size (its content is never read), then every
frame is walked header to header until the end of the file. The frames must agree on version and
sample rate, the last one must be complete, and only an ID3v1 or APEv2 tag may follow them:
anything else is another file hiding behind an mp3 header. The walk stops once the frames add up
to more than the cap, so a low-bitrate "bomb" (hours of audio in a few megabytes) is refused
without being decoded and without walking all of it.
"""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from .container_policy import DURATION_SLACK_S, Container, InvalidMedia, Read

_MAX_ID3_TAGS = 4
_MAX_LEADING_PAD = 64 * 1024
_ID3V1 = 128
_APE_FOOTER = 32

# kbit/s by bitrate index for Layer III; index 0 (free format) and 15 are refused.
_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),  # MPEG-1
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2
    0: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2.5
}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def frame_header(b: bytes) -> Optional[Tuple[int, int, int, int, int]]:
    """``(version, sample_rate_hz, channels, frame_bytes, samples)`` of a Layer III frame header,
    or None when the four bytes are not one."""
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    version, layer = (b[1] >> 3) & 0x3, (b[1] >> 1) & 0x3
    br_index, sr_index = b[2] >> 4, (b[2] >> 2) & 0x3
    if version == 1 or layer != 0x1 or br_index in (0, 15) or sr_index == 3:
        return None
    rate = _RATES[version][sr_index]
    kbps = _BITRATES[version][br_index]
    padding = (b[2] >> 1) & 0x1
    samples = 1152 if version == 3 else 576
    length = samples // 8 * kbps * 1000 // rate + padding
    channels = 1 if (b[3] >> 6) == 0x3 else 2
    return version, rate, channels, length, samples


def _skip_id3v2(read: Read, size: int) -> int:
    pos = 0
    for _ in range(_MAX_ID3_TAGS):
        head = read(pos, 10)
        if head[:3] != b"ID3":
            return pos
        if len(head) < 10 or head[3] not in (2, 3, 4) or any(x & 0x80 for x in head[6:10]):
            raise InvalidMedia("bad id3 tag")
        tag = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        footer = 10 if head[3] == 4 and head[5] & 0x10 else 0
        pos += 10 + tag + footer
        if pos >= size:
            raise InvalidMedia("truncated")
    raise InvalidMedia("too many id3 tags")


def _skip_padding(read: Read, pos: int, size: int) -> int:
    """Some taggers pad with zero bytes between the tag and the first frame."""
    limit = min(size, pos + _MAX_LEADING_PAD)
    while pos < limit:
        chunk = read(pos, min(4096, limit - pos))
        stripped = chunk.lstrip(b"\x00")
        if stripped:
            return pos + len(chunk) - len(stripped)
        pos += len(chunk)
    return pos


def _trailer_ok(read: Read, pos: int, size: int) -> bool:
    """True when the bytes after the last frame are only an APEv2 and/or an ID3v1 tag."""
    end = size
    if end - pos >= _ID3V1 and read(end - _ID3V1, 3) == b"TAG":
        end -= _ID3V1
    if end - pos >= _APE_FOOTER:
        footer = read(end - _APE_FOOTER, _APE_FOOTER)
        if footer[:8] == b"APETAGEX":
            tag = struct.unpack("<I", footer[12:16])[0]
            has_header = struct.unpack("<I", footer[20:24])[0] & 0x80000000
            end -= tag + (_APE_FOOTER if has_header else 0)
    return end == pos


def inspect_mp3(read: Read, size: int, max_decoded_s: float) -> Container:
    start = _skip_padding(read, _skip_id3v2(read, size), size)
    first = frame_header(read(start, 4))
    if first is None:
        raise InvalidMedia("no mpeg frame")
    version, rate, channels = first[0], first[1], first[2]
    cap = max_decoded_s + DURATION_SLACK_S
    pos, samples = start, 0
    while pos < size:
        header = frame_header(read(pos, 4))
        if header is None:
            break
        if header[0] != version or header[1] != rate:
            raise InvalidMedia("inconsistent mpeg frames")
        if pos + header[3] > size:
            raise InvalidMedia("truncated")
        pos += header[3]
        samples += header[4]
        if samples / rate > cap:
            return Container("mp3", rate, channels, samples / rate, None, start)
    if pos < size and not _trailer_ok(read, pos, size):
        raise InvalidMedia("data after the mpeg frames")
    return Container("mp3", rate, channels, samples / rate, None, start)
