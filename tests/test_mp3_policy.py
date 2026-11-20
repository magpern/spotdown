"""The mp3 frame walk: tags skipped by size, every frame complete, nothing hidden after them."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.service.container_policy import InvalidMedia
from app.service.mp3_policy import frame_header, inspect_mp3

from . import media_fixtures as mf


def inspect(data: bytes, cap: float = 1200.0):
    return inspect_mp3(lambda o, n: data[o : o + n], len(data), cap)


def test_frames_after_an_id3_tag_give_the_stream_and_duration() -> None:
    tag = mf.id3v2()
    c = inspect(tag + mf.mp3_frames(50))
    assert (c.media, c.sample_rate_hz, c.channels, c.audio_offset) == ("mp3", 44100, 1, len(tag))
    assert c.duration_s == pytest.approx(50 * 1152 / 44100)
    assert mf.MARKER not in repr(c)


def test_a_real_encoded_mp3_is_accepted(tmp_path: Path) -> None:
    data = mf.real_mp3(tmp_path / "a.mp3", seconds=2.0).read_bytes()
    c = inspect(data)
    assert c.sample_rate_hz == 22050 and c.duration_s == pytest.approx(2.0, abs=0.2)


@pytest.mark.parametrize("trailer", ["id3v1", "ape", "both", "zero_padding_before"])
def test_known_trailers_and_leading_padding_are_accepted(trailer: str) -> None:
    frames = mf.mp3_frames(10)
    ape = b"APETAGEX" + struct.pack("<IIII", 2000, 32, 0, 0) + b"\x00" * 8
    data = {
        "id3v1": frames + b"TAG" + b"\x00" * 125,
        "ape": frames + ape,
        "both": frames + ape + b"TAG" + b"\x00" * 125,
        "zero_padding_before": mf.id3v2() + b"\x00" * 500 + frames,
    }[trailer]
    assert inspect(data).channels == 1


@pytest.mark.parametrize("mode,channels", [(0, 2), (1, 2), (2, 2), (3, 1)])
def test_channel_modes(mode: int, channels: int) -> None:
    assert inspect(mf.mp3_frames(4, mode=mode)).channels == channels


FRAMES = mf.mp3_frames(20)
BAD = {
    "id3_only": mf.id3v2(),
    "id3_size_past_the_file": mf.id3v2(declared=50_000_000) + FRAMES,
    "id3_not_synchsafe": b"ID3\x03\x00\x00\x00\x00\x80\x00" + FRAMES,
    "id3_bad_version": b"ID3\x09\x00\x00\x00\x00\x00\x00" + FRAMES,
    "truncated_last_frame": FRAMES[:-10],
    "polyglot_zip_after_frames": FRAMES + b"PK\x03\x04" + b"\x00" * 300,
    "html_after_id3": mf.id3v2() + b"<html>" * 50,
    "mp4_after_id3": mf.id3v2() + mf.mp4(),
    "free_format": mf.mp3_frames(5, br=0)[:4] + b"\x00" * 400,
    "bad_bitrate_index": mf.mp3_header(br=15) + b"\x00" * 400,
    "reserved_rate": mf.mp3_header(sr=3) + b"\x00" * 400,
    "mixed_rates": mf.mp3_frames(5) + mf.mp3_frames(5, sr=1),
    "mixed_versions": mf.mp3_frames(5) + mf.mp3_frames(5, version=2),
    "layer_ii": bytes([0xFF, 0xFD, 0x90, 0xC0]) + b"\x00" * 400,
    "junk_in_the_middle": mf.mp3_frames(5) + b"\x12" * 7 + mf.mp3_frames(5),
    "ape_size_wrong": FRAMES + b"APETAGEX" + struct.pack("<IIII", 2000, 99, 0, 0) + b"\x00" * 8,
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_malformed_mp3s_are_refused(name: str) -> None:
    with pytest.raises(InvalidMedia):
        inspect(BAD[name])


def test_a_low_bitrate_bomb_is_found_long_without_walking_all_of_it() -> None:
    """8 kbit/s MPEG-2.5 at 8 kHz: 72 ms per 72-byte frame, so 20 minutes fit in 1.2 MB."""
    frames = mf.mp3_frames(20_000, version=0, br=1, sr=2)
    reads = []

    def read(offset: int, count: int) -> bytes:
        reads.append(offset)
        return frames[offset : offset + count]

    c = inspect_mp3(read, len(frames), 60.0)
    assert c.duration_s is not None and c.duration_s > 61.0
    assert max(reads) < len(frames) // 10  # stopped just past the cap


def test_the_frame_header_table() -> None:
    assert frame_header(mf.mp3_header()) == (3, 44100, 1, 417, 1152)
    assert frame_header(mf.mp3_header(version=2, br=8, sr=0, mode=0)) == (2, 22050, 2, 208, 576)
    assert frame_header(b"\xff\xfb") is None


# ---- one case per rule, built so that rule alone decides ----


@pytest.mark.parametrize(
    "head",
    [
        b"\x7f\xfb\x90\x64",  # no sync byte
        b"\xff\x1b\x90\x64",  # the sync bits cut short
        bytes([0xFF, 0xE0 | (1 << 3) | (1 << 1) | 1, 0x90, 0xC0]),  # reserved MPEG version
        bytes([0xFF, 0xE0 | (3 << 3) | (2 << 1) | 1, 0x90, 0xC0]),  # Layer II
        bytes([0xFF, 0xE0 | (3 << 3) | (3 << 1) | 1, 0x90, 0xC0]),  # Layer I
        mf.mp3_header(br=0),  # free format
        mf.mp3_header(br=15),
        mf.mp3_header(sr=3),
    ],
)
def test_frame_headers_that_are_not_layer_iii(head: bytes) -> None:
    assert frame_header(head) is None


def _id3(size_bytes: bytes, version: int = 3, flags: int = 0, footer: bool = False) -> bytes:
    size = (size_bytes[0] << 21) | (size_bytes[1] << 14) | (size_bytes[2] << 7) | size_bytes[3]
    tag = b"ID3" + bytes([version, 0, flags]) + size_bytes + b"\x00" * (size & 0xFFFFFFF)
    return tag + (b"3DI" + bytes([version, 0, flags]) + size_bytes if footer else b"")


def test_an_id3_size_that_is_not_synchsafe_is_refused() -> None:
    assert inspect(_id3(b"\x00\x00\x01\x00") + FRAMES).audio_offset == 10 + 128
    with pytest.raises(InvalidMedia):
        inspect(_id3(b"\x00\x00\x00\x80") + b"\x00" * 128 + FRAMES)  # 0x80: not synchsafe


def test_an_id3v24_footer_is_skipped() -> None:
    tag = _id3(b"\x00\x00\x00\x10", version=4, flags=0x10, footer=True)
    assert inspect(tag + FRAMES).audio_offset == len(tag)


def test_at_most_four_id3_tags_are_skipped() -> None:
    tag = _id3(b"\x00\x00\x00\x10")
    assert inspect(tag * 4 + FRAMES).audio_offset == 4 * len(tag)
    with pytest.raises(InvalidMedia):
        inspect(tag * 5 + FRAMES)


def test_at_most_64_kib_of_padding_before_the_first_frame() -> None:
    assert inspect(mf.id3v2() + b"\x00" * (64 * 1024 - 1) + FRAMES).channels == 1
    with pytest.raises(InvalidMedia):
        inspect(mf.id3v2() + b"\x00" * (64 * 1024 + 1) + FRAMES)


def test_an_apev2_tag_with_a_header_is_accepted() -> None:
    items = b"\x00" * 40
    size = len(items) + 32  # the items and the footer, not the header
    flags = struct.pack("<I", 0x80000000)
    header = b"APETAGEX" + struct.pack("<III", 2000, size, 0) + flags + b"\x00" * 8
    footer = b"APETAGEX" + struct.pack("<III", 2000, size, 0) + flags + b"\x00" * 8
    assert inspect(FRAMES + header + items + footer).channels == 1


def test_an_id3_tag_reaching_the_end_is_truncated() -> None:
    with pytest.raises(InvalidMedia) as exc:
        inspect(mf.id3v2(declared=50_000_000) + FRAMES)
    assert exc.value.detail == "truncated"
