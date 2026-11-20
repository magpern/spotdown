"""The mp4 box walk: known boxes that fit, an MP4 brand, an AAC sound track, honest tables."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest
import soundfile as sf

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


# ---- every sample table must hold what it declares (one oversized count per table kind) ----

TABLE_KINDS = [(b"stts", 8), (b"ctts", 8), (b"stsc", 12), (b"stss", 4), (b"stco", 4), (b"co64", 8)]


@pytest.mark.parametrize("kind,per", TABLE_KINDS, ids=[k.decode() for k, _ in TABLE_KINDS])
def test_each_sample_table_must_hold_its_declared_entries(kind: bytes, per: int) -> None:
    assert inspect(mf.mp4(tables=mf.stbl_table(kind, 3, per, 3))).channels == 2  # honest
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(tables=mf.stbl_table(kind, 4, per, 3)))  # one entry short
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(tables=mf.stbl_table(kind, 50_000_000, per, 1)))  # a count bomb


def test_each_video_track_table_is_checked_too() -> None:
    video = mf.trak(entry=mf.box(b"avc1", b"\x00" * 78), handler=b"vide")
    bomb = mf.trak(
        entry=mf.box(b"avc1", b"\x00" * 78),
        handler=b"vide",
        tables=mf.stbl_table(b"stco", 50_000_000, 4, 1),
    )
    assert inspect(mf.mp4(after=video)).channels == 2
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(after=bomb))


# ---- every track is checked, whatever its handler; mp4a must carry AAC ----

ALAC = mf.mp4a(kind=b"alac", config=b"")
LPCM = mf.box(b"lpcm", b"\x00" * 60)
AVC = mf.box(b"avc1", b"\x00" * 78)


@pytest.mark.parametrize(
    "name,data",
    [
        ("alac_behind_a_text_handler", mf.mp4(before=mf.trak(entry=ALAC, handler=b"text"))),
        ("pcm_behind_a_meta_handler", mf.mp4(before=mf.trak(entry=LPCM, handler=b"meta"))),
        ("alac_behind_a_video_handler", mf.mp4(before=mf.trak(entry=ALAC, handler=b"vide"))),
        ("aac_behind_a_text_handler", mf.mp4(before=mf.trak(handler=b"text"))),
        (
            "a_chapter_text_track",
            mf.mp4(after=mf.trak(entry=mf.box(b"text", b"\x00" * 40), handler=b"text")),
        ),
        ("alac_as_a_second_entry", mf.mp4(before=mf.trak(entry=mf.mp4a() + ALAC, entries=2))),
        ("a_second_track_of_alac", mf.mp4(after=mf.trak(entry=ALAC))),
    ],
)
def test_no_track_may_carry_another_codec(name: str, data: bytes) -> None:
    with pytest.raises(InvalidMedia):
        inspect(data)


def test_a_video_track_with_a_video_entry_is_allowed() -> None:
    assert inspect(mf.mp4(before=mf.trak(entry=AVC, handler=b"vide"))).channels == 2


@pytest.mark.parametrize("declared", [0, 2, 17])
def test_the_entry_count_must_match_the_entries(declared: int) -> None:
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(before=mf.trak(entries=declared)))


@pytest.mark.parametrize(
    "config",
    [
        mf.esds(oti=0x6B),  # MPEG-1 audio (mp3) in an mp4a wrapper
        mf.esds(oti=0x69),  # MPEG-2 audio (mp3)
        mf.esds(oti=0xA5),  # AC-3
        mf.esds(aot=34),  # MPEG-4 "Layer 3" (mp3 on MP4)
        mf.esds(aot=36),  # ALS
        mf.esds(aot=8),  # CELP
        mf.esds(url=True),  # the stream elsewhere
        b"",  # no esds at all
        mf.full(b"esds", b"\x03\x7f\x00"),  # a descriptor longer than its box
        mf.full(b"esds", b"\x04\x05\x40\x15\x00\x00\x00"),  # no ES_Descriptor
    ],
    ids=["mp3", "mpeg2_audio", "ac3", "mp3_on_mp4", "als", "celp", "url", "none", "long", "no_es"],
)
def test_mp4a_must_name_aac(config: bytes) -> None:
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=mf.mp4a(config=config)))


@pytest.mark.parametrize(
    "config",
    [mf.esds(oti=0x40, aot=2), mf.esds(aot=5), mf.esds(aot=29), mf.esds(oti=0x67), mf.esds(aot=1)],
    ids=["lc", "he", "he_v2", "mpeg2_lc", "main"],
)
def test_aac_object_types_are_accepted(config: bytes) -> None:
    assert inspect(mf.mp4(entry=mf.mp4a(config=config))).channels == 2


def test_a_quicktime_v1_entry_with_esds_inside_wave_is_accepted() -> None:
    wave = mf.box(b"wave", mf.esds())
    assert inspect(mf.mp4(entry=mf.mp4a(version=1, config=wave))).channels == 2
    bad = mf.box(b"wave", mf.esds(oti=0x6B))
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=mf.mp4a(version=1, config=bad)))


def test_an_entry_past_its_stsd_is_refused() -> None:
    entry = mf.mp4a()
    short = mf.box(b"mp4a", b"\x00" * 20)  # too short for the fields read
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=short))
    lying = entry[:3] + bytes([entry[3] + 40]) + entry[4:]  # its size runs past stsd
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=lying))


def test_a_quicktime_major_brand_is_refused_even_listing_isom() -> None:
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(brand=b"qt  ", compat=b"isom"))
    assert inspect(mf.mp4(brand=b"isom", compat=b"qt  ")).channels == 2


def _ffmpeg(tmp_path: Path, *args: str) -> bytes:
    src = tmp_path / "src.wav"
    sf.write(str(src), mf.tone(1.0, 22050), 22050, subtype="PCM_16")
    out = tmp_path / "out.mp4"
    base = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
    subprocess.run([*base, *args, str(out)], check=True)
    return out.read_bytes()


def test_real_files_with_other_codecs_are_refused(tmp_path: Path) -> None:
    """The reviewer's reproductions: ALAC behind a ``text`` handler, mp3 inside an mp4a entry."""
    if not mf.has_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    alac = _ffmpeg(tmp_path, "-c:a", "alac", "-f", "mp4")
    with pytest.raises(InvalidMedia):
        inspect(alac)
    at = alac.index(b"hdlr") + 12
    assert alac[at : at + 4] == b"soun"
    with pytest.raises(InvalidMedia):
        inspect(alac[:at] + b"text" + alac[at + 4 :])
    mp3 = _ffmpeg(tmp_path, "-c:a", "libmp3lame", "-f", "mp4")
    with pytest.raises(InvalidMedia):
        inspect(mp3)


# ---- one case per rule, built so that rule alone refuses it ----


def _trun(flags: int, count: int, fields: int) -> bytes:
    payload = struct.pack(">II", flags, count) + b"\x00" * (4 * fields)
    return mf.box(b"moof", mf.box(b"traf", mf.box(b"trun", payload)))


def _frag(moof: bytes) -> bytes:
    return mf.mp4(brand=b"dash", duration=0, extra_top={"moof": moof})


def test_trun_rules() -> None:
    assert inspect(_frag(_trun(0x205, 2, 4))).duration_s is None  # offset, first flags, 2 sizes
    with pytest.raises(InvalidMedia):  # the offset and first-sample flags take room too
        inspect(_frag(_trun(0x205, 2, 2)))
    assert inspect(_frag(_trun(0x300, 2, 4))).duration_s is None  # duration and size, 2 samples
    with pytest.raises(InvalidMedia):  # every per-sample field counts
        inspect(_frag(_trun(0x300, 2, 2)))
    with pytest.raises(InvalidMedia):  # no per-sample field, but more samples than bytes
        inspect(_frag(_trun(0x000, 50_000_000, 0)))


def test_a_variable_size_stsz_must_hold_its_sizes() -> None:
    short = mf.full(b"stsz", struct.pack(">II", 0, 20) + b"\x00" * 40)  # 20 named, 10 present
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(stsz=short))


def test_a_version_1_media_header_is_read() -> None:
    track = mf.trak(timescale=1000, duration=90_000, mdhd_v1=True)
    assert inspect(mf.mp4(track=track)).duration_s == 90.0


def test_a_zero_timescale_is_refused() -> None:
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(track=mf.trak(timescale=0)))


def test_ftyp_must_come_first_and_once() -> None:
    with pytest.raises(InvalidMedia):  # a box before ftyp whose payload reads like a brand
        inspect(mf.box(b"free", b"M4A \x00\x00\x00\x00") + GOOD)
    with pytest.raises(InvalidMedia):
        inspect(GOOD + GOOD[: GOOD.index(b"moov") - 4])  # a second ftyp at the end


def test_a_box_smaller_than_its_header_is_refused() -> None:
    """Inside moov an unknown box is skipped, so a 4-byte box would otherwise let the walk
    resynchronise on the bytes after it."""
    assert inspect(mf.mp4(after=mf.box(b"free", b""))).channels == 2
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(after=b"\x00\x00\x00\x04" + mf.box(b"free", b"")))


def test_an_oversized_movie_header_is_refused() -> None:
    big = mf.box(b"free", b"\x00" * (32 * 1024 * 1024))
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(after=big))


def _esds_raw(payload: bytes) -> bytes:
    return mf.mp4(entry=mf.mp4a(config=mf.full(b"esds", payload)))


def test_esds_descriptor_rules() -> None:
    good = mf.esds()[12:]  # the descriptors, after the box header and version
    assert inspect(_esds_raw(good)).channels == 2
    with pytest.raises(InvalidMedia):  # another descriptor tag first
        inspect(_esds_raw(b"\x13" + good[1:]))
    with pytest.raises(InvalidMedia):  # an expandable length running off the end
        inspect(_esds_raw(b"\x03\x80\x80"))
    with pytest.raises(InvalidMedia):  # an ES_Descriptor too short for its id and flags
        inspect(_esds_raw(b"\x03\x02\x00\x01"))
    with pytest.raises(InvalidMedia):  # the URL flag, followed by a valid config
        inspect(mf.mp4(entry=mf.mp4a(config=mf.esds(url=True))))
    assert inspect(mf.mp4(entry=mf.mp4a(config=mf.esds(asc=b"\x12\x10")))).channels == 2
    with pytest.raises(InvalidMedia):  # a one-byte AudioSpecificConfig (AAC LC, no rate index)
        inspect(mf.mp4(entry=mf.mp4a(config=mf.esds(asc=b"\x12"))))


def test_an_oversized_esds_is_refused() -> None:
    padded = mf.full(b"esds", mf.esds()[12:] + b"\x00" * 1100)
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=mf.mp4a(config=padded)))


def test_a_version_2_sound_entry_is_refused() -> None:
    assert inspect(mf.mp4(entry=mf.mp4a(version=1))).channels == 2
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(entry=mf.mp4a(version=2)))


def test_the_entry_count_range() -> None:
    with pytest.raises(InvalidMedia):  # none declared, none present
        inspect(mf.mp4(before=mf.trak(entry=b"", entries=0)))
    assert inspect(mf.mp4(track=mf.trak(entry=mf.mp4a() * 16, entries=16))).channels == 2
    with pytest.raises(InvalidMedia):
        inspect(mf.mp4(track=mf.trak(entry=mf.mp4a() * 17, entries=17)))


def test_a_sound_entry_shorter_than_its_fields_is_truncated() -> None:
    with pytest.raises(InvalidMedia) as exc:
        inspect(mf.mp4(entry=mf.box(b"mp4a", b"\x00" * 20)))
    assert exc.value.detail == "truncated sample entry"
