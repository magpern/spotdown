"""The WAV checks, the stream sanity limits and a seeded fuzz over all three containers."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from app.service import container_io
from app.service.container_policy import (
    Container,
    InvalidMedia,
    inspect_wav,
    stream_wav_header,
    too_long,
)

from . import media_fixtures as mf


def reader(data: bytes):
    return lambda offset, count: data[offset : offset + count]


def wav_of(data: bytes) -> Container:
    return inspect_wav(reader(data), len(data))


def test_a_pcm_wav_gives_its_stream_and_exact_duration() -> None:
    c = wav_of(mf.wav(rate=16000, channels=2, bits=24, seconds=0.25))
    assert (c.media, c.sample_rate_hz, c.channels, c.bits_per_sample) == ("wav", 16000, 2, 24)
    assert c.duration_s == pytest.approx(0.25)


def test_float_and_extensible_pcm_are_accepted() -> None:
    assert wav_of(mf.wav(tag=3, bits=32)).bits_per_sample == 32
    align = 2
    ext = struct.pack("<HHIIHH", 0xFFFE, 1, 8000, 8000 * align, align, 16)
    ext += struct.pack("<HHI", 22, 16, 0x4) + struct.pack("<H", 1)
    ext += b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    body = b"WAVEfmt " + struct.pack("<I", len(ext)) + ext + b"data" + struct.pack("<I", 800)
    data = b"RIFF" + struct.pack("<I", len(body) + 800) + body + b"\x00" * 800
    assert wav_of(data).sample_rate_hz == 8000


def test_tags_are_skipped_not_read() -> None:
    c = wav_of(mf.wav(chunks=[mf.info_chunk(mf.MARKER)]))
    assert mf.MARKER not in repr(c)


GOOD = mf.wav()
BAD_WAVS = {
    "zero_length": b"",
    "header_only": GOOD[:12],
    "truncated_data": GOOD[:-10],
    "riff_larger_than_file": mf.wav(riff_size=10_000_000),
    "data_larger_than_riff": mf.wav(data_size=10_000_000),
    "data_size_streaming_placeholder": mf.wav(data_size=0xFFFFFFFF),
    "polyglot_zip_appended": GOOD + b"PK\x03\x04" + b"\x00" * 100,
    "trailing_wav_appended": GOOD + GOOD,
    "no_fmt": b"RIFF" + struct.pack("<I", 12) + b"WAVEdata" + struct.pack("<I", 0),
    "adpcm": mf.wav(tag=2),
    "mp3_in_wav": mf.wav(tag=0x55),
    "12_bit": mf.wav(bits=12),
    "float_16_bit": mf.wav(tag=3, bits=16),
    "zero_channels": mf.wav(channels=0),
    "nine_channels": mf.wav(channels=9),
    "rate_4000": mf.wav(rate=4000),
    "rate_384000": mf.wav(rate=384000, seconds=0.01),
    "no_audio": mf.wav(seconds=0),
    "chunk_bomb": mf.wav(chunks=[b"junk" + struct.pack("<I", 0)] * 100),
    "two_fmt": mf.wav(chunks=[b"fmt " + struct.pack("<I", 16) + b"\x00" * 16]),
    "not_riff": b"RIFX" + GOOD[4:],
}


@pytest.mark.parametrize("name", sorted(BAD_WAVS))
def test_malformed_wavs_are_refused(name: str) -> None:
    with pytest.raises(InvalidMedia):
        container_io.inspect("wav", reader(BAD_WAVS[name]), len(BAD_WAVS[name]), 1200.0)


def test_an_inconsistent_block_align_is_refused() -> None:
    data = bytearray(mf.wav(channels=2))
    data[32:34] = struct.pack("<H", 2)  # block_align for 2 x 16 bit is 4
    with pytest.raises(InvalidMedia):
        wav_of(bytes(data))


@pytest.mark.parametrize("rate", [8000, 192000])
@pytest.mark.parametrize("channels", [1, 8])
def test_the_stream_limits_are_inclusive(rate: int, channels: int) -> None:
    c = wav_of(mf.wav(rate=rate, channels=channels, seconds=0.01))
    assert (c.sample_rate_hz, c.channels) == (rate, channels)


def test_too_long_allows_the_decoders_one_second_margin() -> None:
    assert not too_long(Container("wav", 8000, 1, 1201.0), 1200.0)
    assert too_long(Container("wav", 8000, 1, 1201.01), 1200.0)
    assert not too_long(Container("mp4", 8000, 1, None), 1200.0)


def test_a_decoders_wav_stream_header_is_read_without_sizes() -> None:
    head = mf.wav(rate=44100, channels=2, seconds=0.01, data_size=0xFFFFFFFF, riff_size=0)
    assert stream_wav_header(head[:200]) == (44100, 2, 16, 44)
    with pytest.raises(InvalidMedia):
        stream_wav_header(b"RIFF\x00\x00\x00\x00WAVE")


def test_an_unknown_media_and_an_empty_file_are_refused() -> None:
    with pytest.raises(InvalidMedia):
        container_io.inspect("ogg", reader(GOOD), len(GOOD), 1200.0)
    for media in ("wav", "mp3", "mp4"):
        with pytest.raises(InvalidMedia):
            container_io.inspect(media, reader(b""), 0, 1200.0)


# ---- fuzz: truncations and byte flips of valid files only ever end as InvalidMedia ----

SEEDS = {"wav": mf.wav(), "mp3": mf.id3v2() + mf.mp3_frames(30), "mp4": mf.mp4()}


@pytest.mark.parametrize("media", sorted(SEEDS))
def test_fuzzed_containers_only_raise_invalid_media(media: str) -> None:
    seed = SEEDS[media]
    container_io.inspect(media, reader(seed), len(seed), 1200.0)  # the seed itself is valid
    rng = np.random.default_rng(29)
    cases = [seed[:n] for n in range(0, len(seed), max(1, len(seed) // 200))]
    for _ in range(400):
        data = bytearray(seed)
        for at in rng.integers(0, len(data), size=int(rng.integers(1, 6))):
            data[int(at)] = int(rng.integers(0, 256))
        cases.append(bytes(data))
    for data in cases:
        try:
            got = container_io.inspect(media, reader(data), len(data), 1200.0)
        except InvalidMedia:
            continue
        assert got.media == media and 1 <= got.channels <= 8
        assert 8000 <= got.sample_rate_hz <= 192000


def test_exactly_64_chunks_are_allowed_and_65_are_not() -> None:
    junk = b"junk" + struct.pack("<I", 0)
    assert wav_of(mf.wav(chunks=[junk] * 62)).channels == 1  # fmt + 62 + data = 64
    with pytest.raises(InvalidMedia):
        wav_of(mf.wav(chunks=[junk] * 63))


# ---- one case per rule, built so that rule alone refuses it ----


def chunk(name: bytes, body: bytes) -> bytes:
    return name + struct.pack("<I", len(body)) + body + b"\x00" * (len(body) & 1)


def riff(*chunks: bytes) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


FMT = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
DATA = chunk(b"data", b"\x00" * 800)


def test_the_rule_cases_are_valid_without_the_fault() -> None:
    assert wav_of(riff(chunk(b"fmt ", FMT), DATA)).duration_s == pytest.approx(0.05)
    assert wav_of(riff(chunk(b"fmt ", FMT + b"\x00" * 2), DATA)).channels == 1  # cbSize


@pytest.mark.parametrize(
    "name,data",
    [
        ("fmt_shorter_than_16", riff(chunk(b"fmt ", FMT[:14]), DATA)),
        ("fmt_longer_than_256", riff(chunk(b"fmt ", FMT + b"\x00" * 290), DATA)),
        ("a_second_valid_fmt", riff(chunk(b"fmt ", FMT), chunk(b"fmt ", FMT), DATA)),
        ("data_before_fmt", riff(DATA, chunk(b"fmt ", FMT))),
        ("two_data_chunks", riff(chunk(b"fmt ", FMT), DATA, DATA)),
        ("fmt_without_data", riff(chunk(b"fmt ", FMT), chunk(b"junk", b"\x00" * 8))),
    ],
)
def test_each_wav_layout_rule(name: str, data: bytes) -> None:
    with pytest.raises(InvalidMedia):
        wav_of(data)


def test_an_extensible_fmt_needs_the_pcm_or_float_guid() -> None:
    def ext(tail: bytes) -> bytes:
        fmt = struct.pack("<HHIIHH", 0xFFFE, 1, 8000, 16000, 2, 16)
        fmt += struct.pack("<HHI", 22, 16, 0x4) + struct.pack("<H", 1) + tail
        return riff(chunk(b"fmt ", fmt), DATA)

    guid = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    assert wav_of(ext(guid)).channels == 1
    with pytest.raises(InvalidMedia):  # the same sub-format code under another GUID
        wav_of(ext(b"\x11" + guid[1:]))


def test_exact_refuses_a_short_read() -> None:
    from app.service.container_policy import exact

    assert exact(reader(b"abcd"), 1, 3) == b"bcd"
    with pytest.raises(InvalidMedia):
        exact(reader(b"abcd"), 2, 3)


def test_an_empty_file_is_refused_as_empty() -> None:
    for media in ("wav", "mp3", "mp4"):
        with pytest.raises(InvalidMedia) as exc:
            container_io.inspect(media, reader(b""), 0, 1200.0)
        assert exc.value.detail == "empty file"


def test_a_decoders_stream_must_be_riff_and_name_fmt_first() -> None:
    head = mf.wav(rate=44100, channels=2, seconds=0, data_size=0xFFFFFFFF, riff_size=0)
    with pytest.raises(InvalidMedia):
        stream_wav_header(b"RIFX" + head[4:])
    with pytest.raises(InvalidMedia):
        stream_wav_header(head[:12] + chunk(b"data", b"") + head[12:36])
