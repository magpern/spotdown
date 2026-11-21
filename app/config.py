from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .service.cache_policy import GLOBAL_REFERENCE_TTL_S
from .service.retention_policy import ANALYSIS_UNUSED_TTL_S


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    public_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("./data")
    redis_url: str = "redis://localhost:6379/0"
    # False -> API only (do not serve the web UI at GET /).
    serve_ui: bool = True

    # Standalone exposure (ADR 0003). Every standalone route needs a session made from the access
    # token: the owner's file below, else one generated once at data_dir/standalone_token.
    standalone_access_token_file: Optional[Path] = None
    # Host names the standalone app answers to (comma separated DNS names, IPv4 literals or
    # bracketed IPv6 literals, any port). Checked at construction.
    ui_allowed_hosts: str = "localhost,127.0.0.1,[::1]"
    # The exact http(s)://host[:port] browsers use, behind a TLS proxy for example. Empty:
    # http:// plus the request's Host. https makes the cookie __Host- and Secure.
    ui_public_origin: str = ""

    # Conversion defaults
    default_bitrate: int = 320
    default_format: str = "mp3"
    file_ttl_hours: int = 24
    max_duration_drift_s: int = 4

    # Spotify metadata
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Sourcing
    allow_ytdlp_sourcing: bool = False
    source_search_results: int = 5
    # POST /v2/match: how many tracks of one batch are searched at the same time.
    match_concurrency: int = 4

    # Service mode (classbeat plan §3, §8; docs/contract-v2.md). On: only /v2 (behind the service
    # key) and /healthz are served; no UI, /files, /api/v1, Spotify resolution or CORS.
    service_mode: bool = False
    # A secrets file holding the one shared service key. Required in service mode.
    service_key_file: Optional[Path] = None
    # Operator-set hosts the `url` source may fetch from (comma separated DNS names, exact
    # match). Empty: the `url` source is disabled.
    url_allowed_hosts: str = ""
    # Per file, whatever the caller declares (classbeat sends at most 60 MiB): uploads, url and
    # youtube downloads.
    max_upload_bytes: int = 60 * 1024 * 1024
    url_fetch_timeout_s: float = 120.0
    upload_ttl_s: int = 3600
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

    @property
    def standalone_token_path(self) -> Path:
        return self.data_dir / "standalone_token"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spotdown.db"

    @property
    def service_db_path(self) -> Path:
        return self.data_dir / "service.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        if self.service_mode:
            self.uploads_dir.mkdir(parents=True, exist_ok=True)
            self.work_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
