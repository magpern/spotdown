"""Runs the container checks (``container_policy``, ``mp3_policy``, ``mp4_policy``) on a file.

Used twice for every file: when an upload arrives (to refuse a bad file before it is kept) and in
the analysis sandbox before decoding (for every source: upload, url and youtube).
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from . import mp3_policy, mp4_policy
from .container_policy import Container, InvalidMedia, inspect_wav

_WINDOW = 64 * 1024


class _WindowReader:
    """``Read`` over an open file, served from a 64 KiB window so the frame walk stays cheap."""

    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh
        self._start = 0
        self._buf = b""

    def __call__(self, offset: int, count: int) -> bytes:
        if offset < 0 or count < 0:
            raise InvalidMedia("bad offset")
        end = offset + count
        if not (self._start <= offset and end <= self._start + len(self._buf)):
            self._fh.seek(offset)
            self._start, self._buf = offset, self._fh.read(max(count, _WINDOW))
        rel = offset - self._start
        return self._buf[rel : rel + count]


def inspect(media: str, read: mp3_policy.Read, size: int, max_decoded_s: float) -> Container:
    if size <= 0:
        raise InvalidMedia("empty file")
    if media == "wav":
        return inspect_wav(read, size)
    if media == "mp3":
        return mp3_policy.inspect_mp3(read, size, max_decoded_s)
    if media == "mp4":
        return mp4_policy.inspect_mp4(read, size)
    raise InvalidMedia("unsupported container")


def inspect_file(path: Path, media: str, max_decoded_s: float) -> Container:
    """The file's container facts, or InvalidMedia."""
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        return inspect(media, _WindowReader(fh), size, max_decoded_s)
