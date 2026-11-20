"""The mp4 box walk: known boxes that fit, an MP4 brand, an AAC sound track, honest tables."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.service.container_policy import InvalidMedia
from app.service.mp4_policy import inspect_mp4

from . import media_fixtures as mf


def inspect(data: bytes):
    return inspect_mp4(lambda o, n: data[o : o + n], len(data))


def test_a_minimal_m4a_gives_its_stream_and_duration() -> None:
    c = inspect(mf.mp4(rate=44100, channels=2, duration=441000))
    assert (c.media, c.sample_rate_hz, c.channels, c.duration_s) == ("mp4", 44100, 2, 10.0)
    assert mf.MARKER not in repr(c)


@pytest.mark.parametrize("duration", [0, 0xFFFFFFFF])
def test_an_unknown_duration_is_none(duration: int) -> None:
    assert inspect(mf.mp4(duration=duration)).duration_s is None


def test_fragmented_files_with_honest_fragments_are_accepted() -> None:
    # flags 0x200: a size per sample, two samples
    trun = mf.box(b"trun", b"\x00\x00\x02\x00" + struct.pack(">I", 2) + struct.pack(">II", 1, 2))
    moof = mf.box(b"moof", mf.box(b"traf", trun))
    c = inspect(mf.mp4(brand=b"dash", duration=0, extra_top={"moof": moof}))
    assert c.duration_s is None


def test_largesize_and_to_the_end_boxes() -> None:
    base = mf.mp4()
    mdat_at = base.rindex(b"mdat") - 4
    large = base[:mdat_at] + struct.pack(">I4sQ", 1, b"mdat", 16 + 64) + b"\x00" * 64
    assert inspect(large).channels == 2
    to_end = base[:mdat_at] + struct.pack(">I4s", 0, b"mdat") + b"\x00" * 64
    assert inspect(to_end).channels == 2


def test_a_real_encoded_m4a_is_accepted(tmp_path: Path) -> None:
    if not mf.has_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    data = mf.real_m4a(tmp_path / "a.m4a").read_bytes()
    assert inspect(data).sample_rate_hz == 22050


def _huge_stsz() -> bytes:
    return mf.full(b"stsz", struct.pack(">II", 0, 50_000_000))


def _huge_trun() -> bytes:
    payload = b"\x00\x00\x02\x00" + struct.pack(">I", 50_000_000) + b"\x00" * 8
    return mf.box(b"moof", mf.box(b"traf", mf.box(b"trun", payload)))


GOOD = mf.mp4()
BAD = {
    "truncated": GOOD[:-20],
    "ftyp_only": GOOD[:28],
    "polyglot_zip_appended": GOOD + b"PK\x03\x04" + b"\x00" * 60,
    "unknown_top_box": mf.mp4(extra_top={"x": mf.box(b"zzzz", b"\x00" * 8)}),
    "quicktime_brand": mf.mp4(brand=b"qt  ", compat=b""),
    "3gp_brand": mf.mp4(brand=b"3gp4", compat=b"3gp5"),
    "no_ftyp_first": GOOD[GOOD.index(b"moov") - 4 :],
    "two_moov": mf.mp4(moov_count=2),
    "no_sound_track": mf.mp4(handler=b"vide"),
    "encrypted_track": mf.mp4(entry=mf.mp4a(kind=b"enca")),
    "alac_track": mf.mp4(entry=mf.mp4a(kind=b"alac")),
    "sample_entry_v2": mf.mp4(entry=mf.mp4a(version=2)),
    "zero_rate": mf.mp4(rate=0),
    "rate_4000": mf.mp4(rate=4000),
    "nine_channels": mf.mp4(channels=9),
    "stsz_count_bomb": mf.mp4(stsz=_huge_stsz()),
    "stsz_fixed_size_bomb": mf.mp4(stsz=mf.full(b"stsz", struct.pack(">II", 4, 0xFFFFFFFF))),
    "trun_count_bomb": mf.mp4(extra_top={"moof": _huge_trun()}),
    "box_smaller_than_header": GOOD + struct.pack(">I4s", 4, b"free"),
    "box_past_the_file": GOOD + struct.pack(">I4s", 1000, b"free"),
    "no_mdat": GOOD[: GOOD.rindex(b"mdat") - 4],
    "zero_length": b"",
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_malformed_mp4s_are_refused(name: str) -> None:
    with pytest.raises(InvalidMedia):
        inspect(BAD[name])


def test_a_box_count_bomb_is_refused() -> None:
    frees = mf.box(b"free", b"") * 9000
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(extra_top={"free": frees}))
