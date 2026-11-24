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
    # Standalone only: False -> API only (do not serve the web UI at GET /). Ignored in service
    # mode, so a stray SERVE_UI=true never turns the UI on there. SPOTDOWN_UI overrides it.
    serve_ui: bool = True
    # The web UI's own switch (ADR 0004), independent of SERVICE_MODE: on or off. Unset: off in
    # service mode, SERVE_UI in standalone mode. In service mode the UI is the static page only; it
    # analyses through /v2 with the service key the user types in (never stored).
    spotdown_ui: Optional[bool] = None
    # Local-file analysis in the standalone UI (ADR 0002): the deadline on reading the body
    # (60 MiB in 150 s is about 0.4 MiB/s).
    standalone_upload_read_s: float = Field(default=150.0, gt=0)

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

    # Service mode (classbeat plan §3, §8; docs/contract-v2.md). On: /v2 (behind the service key)
    # and /healthz, plus the static UI page only with SPOTDOWN_UI=on; never /files, /api/v1,
    # Spotify resolution or CORS.
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

    @property
    def ui_enabled(self) -> bool:
        """Whether the web UI is served (ADR 0004). In service mode only an explicit
        SPOTDOWN_UI=on turns it on."""
        if self.service_mode:
            return self.spotdown_ui is True
        return self.serve_ui if self.spotdown_ui is None else self.spotdown_ui

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

    @property
    def audio_dir(self) -> Path:
        """The mp3 files of ``POST /v2/audio``, kept ``audio_ttl_s``."""
        return self.data_dir / "audio"

    @property
    def standalone_uploads_dir(self) -> Path:
        """The standalone local-file route's one slot: its lock file, the upload in transit and
        the sandbox's work directory (ADR 0002, Decision 4)."""
        return self.data_dir / "standalone_uploads"

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        if self.service_mode:
            self.uploads_dir.mkdir(parents=True, exist_ok=True)
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self.audio_dir.mkdir(parents=True, exist_ok=True)
        elif self.ui_enabled:
            self.standalone_uploads_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
