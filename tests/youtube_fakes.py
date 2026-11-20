"""A fake yt-dlp (``YoutubeDL`` stand-in) and a youtube-enabled service stack. No network: the
fake answers from a table, and every call is recorded so tests can prove what was (not) done."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.service.worker import execute_job
from app.youtube.ytdlp_client import YtDlpClient

from .cache_helpers import Stack

M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00isomiso2" + b"\x00" * 64


class DownloadError(Exception):
    """Stands in for yt_dlp.utils.DownloadError: yt-dlp reports failures as messages."""


def entry(video_id: str, title: str, channel: str, duration: float, **extra: Any) -> Dict:
    return {"id": video_id, "title": title, "channel": channel, "duration": duration, **extra}


class FakeWorld:
    """What the fake YouTube holds, and what was asked of it."""

    def __init__(self) -> None:
        self.entries: List[Any] = []
        self.search_error: Optional[str] = None
        self.download_error: Optional[str] = None
        self.infos: Dict[str, Dict[str, Any]] = {}
        self.audio: bytes = M4A + b"audio-1"
        self.calls: List[tuple] = []
        self.opened: List[Dict[str, Any]] = []

    def factory(self, opts: Dict[str, Any]) -> "FakeYoutubeDL":
        self.opened.append(opts)
        return FakeYoutubeDL(self, opts)

    def client(self) -> YtDlpClient:
        return YtDlpClient(self.factory)

    @property
    def searches(self) -> List[str]:
        return [c[1] for c in self.calls if c[0] == "extract_info" and c[1].startswith("ytsearch")]

    @property
    def downloads(self) -> List[str]:
        return [c[1] for c in self.calls if c[0] == "download"]


class FakeYoutubeDL:
    def __init__(self, world: FakeWorld, opts: Dict[str, Any]) -> None:
        self.world = world
        self.opts = opts

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> Any:
        assert download is False, "the service never lets yt-dlp download during extraction"
        self.world.calls.append(("extract_info", url))
        if url.startswith("ytsearch"):
            if self.world.search_error:
                raise DownloadError(self.world.search_error)
            return {"_type": "playlist", "entries": list(self.world.entries)}
        assert url.startswith("https://www.youtube.com/watch?v="), url
        if self.world.download_error:
            raise DownloadError(self.world.download_error)
        video_id = url.rsplit("=", 1)[1]
        return dict(self.world.infos.get(video_id) or {"id": video_id, "duration": 12.0})

    def process_ie_result(self, info: Dict[str, Any], download: bool = True) -> Dict:
        self.world.calls.append(("download", info["id"]))
        out = Path(self.opts["outtmpl"].replace("%(ext)s", "m4a"))
        out.write_bytes(self.world.audio)
        return info


class YoutubeStack(Stack):
    """The cache-test stack with the youtube source on and yt-dlp faked."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        overrides.setdefault("allow_ytdlp_sourcing", True)
        super().__init__(tmp_path, **overrides)
        self.world = FakeWorld()
        self.client.app.state.youtube = self.world.client()  # type: ignore[attr-defined]

    def _run(self, job_id: str) -> None:
        execute_job(
            job_id,
            self.settings,
            self.store,
            fetcher=self._fetcher,
            analyser=self._analyser,
            youtube=self.world.client(),
        )

    def job(self, res: Any, scope: Optional[str] = None) -> Dict[str, Any]:
        from .service_helpers import AUTH

        params = {"cache_scope": scope} if scope else {}
        job_id = res.json()["job_id"]
        return self.client.get(f"/v2/jobs/{job_id}", headers=AUTH, params=params).json()

    def done_analysis_id(self, res: Any, scope: Optional[str] = None) -> str:
        job = self.job(res, scope)
        assert job["status"] == "done", job
        return job["analysis_id"]

    def match(
        self,
        tracks: List[Dict[str, Any]],
        scope: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        from .service_helpers import AUTH

        body: Dict[str, Any] = {"tracks": tracks}
        if scope is not None:
            body["cache_scope"] = scope
        return self.client.post("/v2/match", headers={**AUTH, **(headers or {})}, json=body)
