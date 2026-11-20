from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    public_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("./data")
    redis_url: str = "redis://localhost:6379/0"

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

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spotdown.db"

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
