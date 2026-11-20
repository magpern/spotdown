from __future__ import annotations

import shutil
from pathlib import Path

from .config import get_settings


def publish(src: Path, track_id: str, bitrate: int, fmt: str) -> str:
    """Move a finished file into the public files dir; return its stored file name.

    The name includes the bitrate so different-quality variants are distinct files.
    """
    settings = get_settings()
    file_name = f"{track_id}-{bitrate}.{fmt}"
    dest = settings.files_dir / file_name
    shutil.move(str(src), str(dest))
    return file_name


def public_url(file_name: str) -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/files/{file_name}"
