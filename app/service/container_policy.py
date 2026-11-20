"""Whether a file really is the container its first bytes claim, and its stream facts (pure).

``media_policy.sniff`` recognises a container by its first bytes; this module then checks the
whole structure before any decoder sees the file: every chunk, frame or box must fit inside the
file, nothing unknown may follow the audio (a polyglot), and the stream must be sane (sample rate,
channels, sample format). It reads through a ``Read`` callable and never allocates in proportion
to a size the file declares, so a hostile header cannot make it slow or large. Every refusal is
``InvalidMedia``; the file name, tags and metadata are never read into the result.

WAV is checked here; mp3 and mp4 in ``mp3_policy`` and ``mp4_policy``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

# (offset, count) -> at most ``count`` bytes from ``offset``; fewer at the end of the file.
Read = Callable[[int, int], bytes]

MIN_SAMPLE_RATE_HZ = 8_000
MAX_SAMPLE_RATE_HZ = 192_000
MAX_CHANNELS = 8
# A container's own duration may exceed the decoded one by encoder padding; the decoder decodes at
# most the cap plus this much, so a file is refused before decoding only past the same margin.
DURATION_SLACK_S = 1.0

_WAV_MAX_CHUNKS = 64
_WAV_PCM, _WAV_FLOAT, _WAV_EXTENSIBLE = 0x0001, 0x0003, 0xFFFE
# The GUID tail shared by the KSDATAFORMAT_SUBTYPE_* sub-formats of WAVE_FORMAT_EXTENSIBLE.
_GUID_TAIL = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
_PCM_BITS = (8, 16, 24, 32)
_FLOAT_BITS = (32, 64)


class InvalidMedia(Exception):
    """The file is not a well-formed mp3, mp4 or wav. ``detail`` is a fixed phrase, never a
    byte of the file."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Container:
    media: str
    sample_rate_hz: int
    channels: int
    # The duration the structure declares (None when it cannot be known without decoding, as in a
    # fragmented mp4). For mp3 the frame walk stops once past the cap, so it is then a lower bound.
    duration_s: Optional[float]
    bits_per_sample: Optional[int] = None
    audio_offset: int = 0  # mp3: where the first frame starts (after an ID3v2 tag)


def check_stream(sample_rate_hz: int, channels: int) -> None:
    if not MIN_SAMPLE_RATE_HZ <= sample_rate_hz <= MAX_SAMPLE_RATE_HZ:
        raise InvalidMedia("sample rate out of range")
    if not 1 <= channels <= MAX_CHANNELS:
        raise InvalidMedia("channel count out of range")


def too_long(container: Container, max_decoded_s: float) -> bool:
    """True when the structure alone shows more audio than the decoder may decode."""
    return container.duration_s is not None and (
        container.duration_s > max_decoded_s + DURATION_SLACK_S
    )


def exact(read: Read, offset: int, count: int) -> bytes:
    data = read(offset, count)
    if len(data) != count:
        raise InvalidMedia("truncated")
    return data


def parse_wav_fmt(body: bytes) -> Tuple[int, int, int, int]:
    """``(sample_rate_hz, channels, bits_per_sample, block_align)`` of a PCM or float fmt chunk."""
    if len(body) < 16:
        raise InvalidMedia("short fmt chunk")
    tag, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", body[:16])
    if tag == _WAV_EXTENSIBLE:
        if len(body) < 40 or body[26:40] != _GUID_TAIL:
            raise InvalidMedia("unsupported wav sub-format")
        tag = struct.unpack("<H", body[24:26])[0]
    if tag == _WAV_PCM:
        allowed = _PCM_BITS
    elif tag == _WAV_FLOAT:
        allowed = _FLOAT_BITS
    else:
        raise InvalidMedia("compressed wav")
    if bits not in allowed:
        raise InvalidMedia("unsupported sample size")
    check_stream(rate, channels)
    if align != channels * bits // 8 or byte_rate != rate * align:
        raise InvalidMedia("inconsistent wav format")
    return rate, channels, bits, align


def inspect_wav(read: Read, size: int) -> Container:
    head = exact(read, 0, 12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise InvalidMedia("not a wav file")
    riff_end = 8 + struct.unpack("<I", head[4:8])[0]
    # The RIFF chunk is the whole file: shorter is truncated, longer hides other content.
    if riff_end > size:
        raise InvalidMedia("truncated")
    if size - riff_end > 1:
        raise InvalidMedia("data after the wav chunk")
    fmt: Optional[Tuple[int, int, int, int]] = None
    data_size: Optional[int] = None
    pos, chunks = 12, 0
    while pos + 8 <= riff_end:
        chunks += 1
        if chunks > _WAV_MAX_CHUNKS:
            raise InvalidMedia("too many chunks")
        cid = exact(read, pos, 8)
        name, length = cid[:4], struct.unpack("<I", cid[4:8])[0]
        body_at = pos + 8
        if body_at + length > riff_end:
            raise InvalidMedia("truncated")
        if name == b"fmt ":
            if fmt is not None or length > 256:
                raise InvalidMedia("bad fmt chunk")
            fmt = parse_wav_fmt(exact(read, body_at, length))
        elif name == b"data":
            if fmt is None or data_size is not None:
                raise InvalidMedia("data chunk out of order")
            data_size = length
        pos = body_at + length + (length & 1)
    if fmt is None or data_size is None:
        raise InvalidMedia("no audio data")
    rate, channels, bits, align = fmt
    if data_size < align:
        raise InvalidMedia("no audio data")
    return Container("wav", rate, channels, data_size / float(rate * align), bits)


def stream_wav_header(head: bytes) -> Tuple[int, int, int, int]:
    """``(sample_rate_hz, channels, bits_per_sample, data_offset)`` of a WAV stream written by a
    decoder to a pipe, whose sizes are placeholders. Only the chunks before ``data`` are read."""
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise InvalidMedia("not a wav stream")
    pos = 12
    fmt: Optional[Tuple[int, int, int, int]] = None
    for _ in range(_WAV_MAX_CHUNKS):
        if pos + 8 > len(head):
            break
        name, length = head[pos : pos + 4], struct.unpack("<I", head[pos + 4 : pos + 8])[0]
        if name == b"data":
            if fmt is None:
                raise InvalidMedia("data chunk out of order")
            return fmt[0], fmt[1], fmt[2], pos + 8
        if pos + 8 + length > len(head):
            break
        if name == b"fmt ":
            fmt = parse_wav_fmt(head[pos + 8 : pos + 8 + length])
        pos += 8 + length + (length & 1)
    raise InvalidMedia("no wav stream header")
