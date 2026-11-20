"""Only mp3, mp4 and wav are recognised, by their bytes."""

from __future__ import annotations

import pytest

from app.service.media_policy import sniff


@pytest.mark.parametrize(
    "head,media",
    [
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", "wav"),
        (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00", "mp4"),
        (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00", "mp4"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", "mp3"),
        (b"\xff\xfb\x90\x64\x00", "mp3"),  # MPEG-1 layer III
        (b"\xff\xf3\x84\x64\x00", "mp3"),  # MPEG-2 layer III
        (b"\xff\xe3\x18\xc4\x00", "mp3"),  # MPEG-2.5 layer III
        (b"\xff\xfd\x90\x64\x00", None),  # layer II
        (b"\xff\xf1\x50\x80\x00", None),  # AAC ADTS (layer bits 00)
        (b"\xff\xeb\x90\x64\x00", None),  # reserved MPEG version
        (b"RIFF\x24\x00\x00\x00AVI LIST", None),
        (b"OggS\x00\x02", None),
        (b"fLaC\x00\x00", None),
        (b"<html><body>", None),
        (b"#!/bin/sh\n", None),
        (b"", None),
        (b"\xff", None),
    ],
)
def test_sniff(head: bytes, media) -> None:
    assert sniff(head) == media
