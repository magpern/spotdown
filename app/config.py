from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    public_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("./data")
    redis_url: str = "redis://localhost:6379/0"
    # False -> API only (do not serve the web UI at GET /).
    serve_ui: bool = True

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
    max_upload_bytes: int = 100 * 1024 * 1024
    url_fetch_timeout_s: float = 120.0
    upload_ttl_s: int = 3600
    # Analysis sandbox rlimits (inside the container's own ceiling).
    analysis_max_memory_mb: int = 3072
    analysis_max_cpu_s: int = 600
    analysis_wall_timeout_s: int = 900
    analysis_max_decoded_s: float = 1200.0

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
