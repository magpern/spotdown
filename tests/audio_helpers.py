"""A service stack for ``POST /v2/audio``: yt-dlp faked (``youtube_fakes``), the transcode faked
unless a test asks for the real one, and a clock the test moves. No network."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.service import sandbox
from app.service.audio_job import execute_audio_job
from app.service.store import Store
from app.service.worker import limits_from

from .service_helpers import AUTH
from .youtube_fakes import YoutubeStack

VIDEO = "dQw4w9WgXcQ"
REF = f"youtube:{VIDEO}"
# Bytes that look like an mp3 frame to the tagger; only the fake transcoder writes them.
FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 413


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class AudioStack(YoutubeStack):
    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        super().__init__(tmp_path, **overrides)
        self.clock = Clock()
        self.store = Store(self.settings.service_db_path, clock=self.clock)
        self.client.app.state.store = self.store  # type: ignore[attr-defined]
        self.transcodes: List[Dict[str, Any]] = []
        self.real_transcode = False

    def _transcode(
        self, src: Path, media: str, dest: Path, bitrate: int, limits: sandbox.Limits
    ) -> None:
        self.transcodes.append({"media": media, "bitrate": bitrate, "src": src.read_bytes()})
        dest.write_bytes(FAKE_MP3)

    def _run(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None or job["source_kind"] != "audio":
            return super()._run(job_id)
        kwargs: Dict[str, Any] = {"limits": limits_from(self.settings), "clock": self.clock}
        if not self.real_transcode:
            kwargs["transcoder"] = self._transcode
        execute_audio_job(job_id, self.settings, self.store, self.world.client(), **kwargs)

    def request(
        self, body: Optional[Dict[str, Any]] = None, key: str = "k-1", **headers: str
    ) -> Any:
        payload = {"source_ref": REF} if body is None else body
        sent = {**AUTH, "Idempotency-Key": key, **headers}
        return self.client.post("/v2/audio", headers=sent, json=payload)

    def poll(self, res: Any) -> Dict[str, Any]:
        return self.client.get(f"/v2/jobs/{res.json()['job_id']}", headers=AUTH).json()

    def audio_id(self, res: Any) -> str:
        job = self.poll(res)
        assert job["status"] == "done", job
        return job["audio_id"]

    def fetch(self, audio_id: str) -> Any:
        return self.client.get(f"/v2/audio/{audio_id}", headers=AUTH)

    def kept_files(self) -> List[Path]:
        return sorted(p for p in self.settings.audio_dir.rglob("*") if p.is_file())
