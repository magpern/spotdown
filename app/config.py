from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .service.cache_policy import GLOBAL_REFERENCE_TTL_S
from .service.retention_policy import ANALYSIS_UNUSED_TTL_S


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    data_dir: Path = Path("./data")
    redis_url: str = "redis://localhost:6379/0"

    # Deprecated, ignored (ADR 0005 Decision 2): accepted with any value, even empty, for one
    # release so that old compose and .env files still start; nothing reads them, and the api
    # logs one start-up warning that names them (never a value). The release after removes them.
    service_mode: Optional[str] = Field(default=None, repr=False)
    serve_ui: Optional[str] = Field(default=None, repr=False)
    spotdown_ui: Optional[str] = Field(default=None, repr=False)

    # Spotify link resolution, POST /v2/spotify/resolve (spotdown#76): SPOTIFY_CLIENT_ID and a
    # secrets file with the client secret (never the value). With both the Web API is used; with
    # neither, Spotify's public pages; one alone stops the start-up.
    spotify_client_id: str = ""
    spotify_client_secret_file: Optional[Path] = None

    # Sourcing
    # YouTube sourcing (yt-dlp): on by default (owner, 2026-11-24, spotdown#86). False turns every
    # YouTube path off: POST /v2/match, source_ref youtube:... and POST /v2/audio.
    allow_ytdlp_sourcing: bool = True
    source_search_results: int = 5
    # POST /v2/match: how many tracks of one batch are searched at the same time.
    match_concurrency: int = 4

    # The backend (docs/contract-v2.md, ADR 0005): /v2 behind a caller key, and /healthz.
    # A secrets file holding classbeat's service key. Required: the api does not start without it.
    service_key_file: Optional[Path] = None
    # Optional: a secrets file with the UI image's own caller key (ADR 0005, Decision 3). Same
    # rules and the same rights on /v2 as the key above, and it must differ from it.
    ui_service_key_file: Optional[Path] = None
    # Operator-set hosts the `url` source may fetch from (comma separated DNS names, exact
    # match). Empty: the `url` source is disabled.
    url_allowed_hosts: str = ""
    # Per file, whatever the caller declares (classbeat sends at most 60 MiB): uploads, url and
    # youtube downloads.
    max_upload_bytes: int = 60 * 1024 * 1024
    url_fetch_timeout_s: float = 120.0
    upload_ttl_s: int = 3600
    # POST /v2/audio (ADR 0005): an mp3 is kept this long after the last job that handed it out,
    # then swept. At least 10 minutes, so a caller can fetch what its job made.
    audio_ttl_s: float = Field(default=86400.0, ge=600)
    # Analysis sandbox rlimits (inside the container's own ceiling).
    analysis_max_memory_mb: int = 3072
    analysis_max_cpu_s: int = 600
    analysis_wall_timeout_s: int = 900
    analysis_max_decoded_s: float = 1200.0
    # Wall time for decoding one file in the sandbox (a slow decode is a hostile file).
    analysis_max_decode_s: float = Field(default=120.0, gt=0)
    # Lifetime of the global rows that hold Spotify-catalogue-derived references: the match
    # record of a chosen video and the reference of a global (youtube) alignment (ADR 0040 §7).
    # Expired rows are never served and are purged. At least a minute: a lifetime of 0 would
    # expire an alignment the moment a job ends on it.
    global_reference_ttl_s: float = Field(default=GLOBAL_REFERENCE_TTL_S, ge=60)

    # A global (public-source) analysis, its source links and match record are purged after this
    # long without a read, or when the analyzer version changes (ADR 0040 §7). At least a day.
    global_analysis_ttl_s: float = Field(default=ANALYSIS_UNUSED_TTL_S, ge=86400)
    # A scoped analysis (with its links and alignments) is purged after this long without a read,
    # or when the analyzer version changes; a scoped job record after this long unchanged
    # (spotdown#40: a scope that is never deleted must not keep everything). At least a day.
    scoped_retention_ttl_s: float = Field(default=ANALYSIS_UNUSED_TTL_S, ge=86400)

    def deprecated_set(self) -> List[str]:
        """The deprecated settings given a value (names only), in a fixed order."""
        values = {
            "SERVICE_MODE": self.service_mode,
            "SERVE_UI": self.serve_ui,
            "SPOTDOWN_UI": self.spotdown_ui,
        }
        return [name for name, value in values.items() if value is not None]

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def service_db_path(self) -> Path:
        return self.data_dir / "service.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def audio_dir(self) -> Path:
        """The mp3 files of ``POST /v2/audio``, kept ``audio_ttl_s``."""
        return self.data_dir / "audio"

    def ensure_dirs(self) -> None:
        """Only what the backend uses: tmp, the uploads, the job work directories and the mp3s."""
        for path in (self.tmp_dir, self.uploads_dir, self.work_dir, self.audio_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
