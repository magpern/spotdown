"""The store-free checks every uploaded file passes, shared by ``PUT /v2/uploads`` and the
standalone local-file route (ADR 0002, Decision 4). Each check has this one implementation.

- the declared ``Content-Length`` against the byte cap, before anything is read;
- streaming the body to a given path under the cap, with its sha256 and first bytes;
- ``media_policy.sniff`` on those bytes (only mp3, mp4 and wav; the name and type are ignored);
- ``container_io.inspect_file`` on the whole file, off the event loop (truncated, malformed or
  mixed files, insane streams and, for mp3, an over-long decoded length are refused).

Callers map ``Refused`` to their own answer shape and delete the file on any exit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

import anyio.to_thread

from . import container_io, media_policy
from .container_policy import InvalidMedia


class Refused(Exception):
    """An upload refused by a check: ``kind`` is ``too_large`` or ``unsupported_media_type``, and
    ``detail`` a fixed message (never anything taken from the file)."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(kind)
        self.kind = kind
        self.detail = detail


TOO_LARGE = ("too_large", "the upload is larger than the limit")
NOT_AUDIO = ("unsupported_media_type", "only mp3, mp4 and wav are accepted")
MALFORMED = ("unsupported_media_type", "not a well-formed mp3, mp4 or wav file")


@dataclass(frozen=True)
class Received:
    sha256: str
    size: int
    head: bytes


def declared_too_large(content_length: Optional[str], max_bytes: int) -> bool:
    """A declared length that is not a plain number, or is over the cap."""
    return content_length is not None and (
        not content_length.isdigit() or int(content_length) > max_bytes
    )


async def stream_to(chunks: AsyncIterator[bytes], path: Path, max_bytes: int) -> Received:
    """Writes the body to ``path``; ``Refused`` (too large) as soon as it passes the cap (the
    rest is never read). The caller deletes ``path`` whatever happens."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    with path.open("wb") as out:
        async for chunk in chunks:
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise Refused(*TOO_LARGE)
            if len(head) < media_policy.SNIFF_BYTES:
                head += chunk[: media_policy.SNIFF_BYTES - len(head)]
            digest.update(chunk)
            out.write(chunk)
    return Received(digest.hexdigest(), size, head)


async def check_file(path: Path, head: bytes, max_decoded_s: float) -> str:
    """The container (``mp3``, ``mp4`` or ``wav``) of a received file, or
    ``Refused`` (unsupported media)."""
    media = media_policy.sniff(head)
    if media is None:
        raise Refused(*NOT_AUDIO)
    try:  # the whole structure, off the event loop (it reads the file)
        await anyio.to_thread.run_sync(container_io.inspect_file, path, media, max_decoded_s)
    except InvalidMedia:
        raise Refused(*MALFORMED) from None
    return media
