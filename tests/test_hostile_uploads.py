"""PUT /v2/uploads with hostile bodies: a typed 415, and no file, upload row or idempotency record.

Every body passes the first-bytes sniff (or is empty); only the whole-structure check refuses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service import container_io
from app.service.store import Store

from . import media_fixtures as mf
from .service_helpers import AUTH, SCOPE, client_for, service_settings, wav_bytes

OCTET = {**AUTH, "Content-Type": "application/octet-stream"}


@pytest.fixture()
def settings(tmp_path: Path):
    return service_settings(tmp_path, max_upload_bytes=2_000_000, analysis_max_decoded_s=60.0)


def _rows(settings) -> dict:
    store = Store(settings.service_db_path)
    with store._conn() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("uploads", "idempotency", "jobs")
        }


def assert_nothing_kept(settings) -> None:
    left = [p for p in settings.uploads_dir.rglob("*") if p.is_file()]
    assert left == []
    assert _rows(settings) == {"uploads": 0, "idempotency": 0, "jobs": 0}


HOSTILE = {
    "zero_length": b"",
    "wav_truncated": mf.wav(seconds=1.0)[:-100],
    "wav_polyglot_zip": mf.wav() + b"PK\x03\x04" + b"\x00" * 64,
    "wav_adpcm": mf.wav(tag=2),
    "wav_rate_4000": mf.wav(rate=4000),
    "wav_16_channels": mf.wav(channels=16, seconds=0.05),
    "wav_data_size_bomb": mf.wav(data_size=0xFFFFFFFF),
    "mp3_id3_then_html": mf.id3v2() + b"<html>" * 100,
    "mp3_id3_size_bomb": mf.id3v2(declared=200_000_000) + mf.mp3_frames(10),
    "mp3_truncated": mf.mp3_frames(10)[:-50],
    "mp3_zip_after_frames": mf.mp3_frames(10) + b"PK\x03\x04" + b"\x00" * 300,
    "mp3_bare_sync_bytes": b"\xff\xfb\x90\x64" + b"\x00" * 100,
    "mp4_header_then_zip": b"\x00\x00\x00\x18ftypM4A " + b"PK\x03\x04" * 64,
    "mp4_truncated": mf.mp4()[:-20],
    "mp4_quicktime": mf.mp4(brand=b"qt  ", compat=b""),
    "mp4_table_bomb": mf.mp4(stsz=mf.full(b"stsz", b"\x00\x00\x00\x00" + b"\xff" * 4)),
    "mp4_encrypted": mf.mp4(entry=mf.mp4a(kind=b"enca")),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_uploads_are_refused_and_leave_nothing(settings, name: str) -> None:
    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}&filename={mf.MARKER}.wav",
        headers=OCTET,
        content=HOSTILE[name],
    )
    assert res.status_code == 415
    body = res.json()
    assert body["error"]["code"] == "unsupported_media_type"
    assert mf.MARKER not in res.text
    assert_nothing_kept(settings)


def test_an_overlong_file_is_kept_for_the_job_to_refuse(settings) -> None:
    """The decoded-length cap is the job's (processing_failed, out_of_memory), as before."""
    data = mf.mp3_frames(2_000, version=0, br=1, sr=2)  # 144 s, over the 60 s cap
    res = client_for(settings).put(f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=data)
    assert res.status_code == 201


def test_a_failing_check_still_leaves_nothing(settings, monkeypatch, tmp_path) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(container_io, "inspect_file", broken)
    client = client_for(settings)
    with pytest.raises(RuntimeError):
        client.put(f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=wav_bytes(tmp_path))
    assert_nothing_kept(settings)


def test_the_check_runs_on_every_upload(settings, monkeypatch, tmp_path) -> None:
    seen = []
    real = container_io.inspect_file

    def spy(path, media, cap):
        seen.append((media, cap))
        return real(path, media, cap)

    monkeypatch.setattr(container_io, "inspect_file", spy)
    res = client_for(settings).put(
        f"/v2/uploads?cache_scope={SCOPE}", headers=OCTET, content=wav_bytes(tmp_path)
    )
    assert res.status_code == 201 and seen == [("wav", 60.0)]


def test_the_byte_cap_is_60_mib_by_default(tmp_path: Path) -> None:
    assert service_settings(tmp_path).max_upload_bytes == 2 * 1024 * 1024  # the tests' own
    from app.config import Settings

    assert Settings.model_fields["max_upload_bytes"].default == 60 * 1024 * 1024
    assert Settings.model_fields["analysis_max_decode_s"].default == 120.0
