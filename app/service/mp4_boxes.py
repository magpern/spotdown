"""The box layout of an mp4 file (pure): every box fits its parent, a shared budget bounds the walk.

Used by ``mp4_policy`` and ``mp4_entry_policy``.
"""

from __future__ import annotations

import struct
from typing import Iterator, List, Optional, Tuple

from .container_policy import InvalidMedia, Read, exact

Box = Tuple[bytes, int, int]  # (type, payload offset, end offset)


def boxes(read: Read, start: int, end: int, budget: List[int]) -> Iterator[Box]:
    """The boxes laid out in ``[start, end)``; each must fit, and the whole range must be boxes."""
    pos = start
    while pos < end:
        budget[0] -= 1
        if budget[0] < 0:
            raise InvalidMedia("too many boxes")
        head = exact(read, pos, 8) if end - pos >= 8 else b""
        if not head:
            raise InvalidMedia("truncated box")
        size, kind = struct.unpack(">I4s", head)
        payload = pos + 8
        if size == 1:
            size = struct.unpack(">Q", exact(read, pos + 8, 8))[0]
            payload = pos + 16
        elif size == 0:
            size = end - pos  # to the end of its parent
        if size < payload - pos or pos + size > end:
            raise InvalidMedia("truncated box")
        yield kind, payload, pos + size
        pos += size


def child(read: Read, parent: Box, kind: bytes, budget: List[int]) -> Optional[Box]:
    for box in boxes(read, parent[1], parent[2], budget):
        if box[0] == kind:
            return box
    return None


def need(box: Optional[Box]) -> Box:
    if box is None:
        raise InvalidMedia("incomplete track")
    return box
