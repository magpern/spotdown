"""The settings templates start the backend as documented (spotdown#79 review, B1): ``.env.example``
copied as ``.env`` (README, "Local development") and ``deploy/spotdown.env.example`` copied as
``spotdown.env`` (the runbook) build the app with nothing but a valid service key file. An empty
``Optional[Path]`` setting means unset; a set path that cannot be read still stops the start-up."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

import pytest

from app.config import Settings
from app.main import create_app
from app.service.service_key import ServiceKeyError

from .service_helpers import TEST_KEY

ROOT = Path(__file__).resolve().parent.parent
PATH_SETTINGS = ["SERVICE_KEY_FILE", "UI_SERVICE_KEY_FILE", "SPOTIFY_CLIENT_SECRET_FILE"]
UI_KEY = "ui-key-" + "7" * 40


def _clean_env(monkeypatch) -> None:
    """No setting from the test process: only the template (and what a test sets) counts."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _key(path: Path, value: str = TEST_KEY) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def _paths(app) -> List[str]:
    return sorted(getattr(r, "path", "") for r in app.router.routes)


def test_the_dev_env_example_starts_the_backend(tmp_path: Path, monkeypatch) -> None:
    """``cp .env.example .env`` plus the service key file at the template's own path."""
    _clean_env(monkeypatch)
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    _key(tmp_path / "secrets" / "spotdown_service_key")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.ui_service_key_file is None
    assert settings.spotify_client_secret_file is None
    app = create_app(settings)
    assert "/healthz" in _paths(app)


def test_the_dev_compose_env_starts_the_backend(tmp_path: Path, monkeypatch) -> None:
    """docker-compose.yml: ``env_file: .env`` from the template, with the compose environment's
    two key files on top (they win over the file)."""
    _clean_env(monkeypatch)
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    monkeypatch.setenv("SERVICE_KEY_FILE", str(_key(tmp_path / "run" / "service")))
    monkeypatch.setenv("UI_SERVICE_KEY_FILE", str(_key(tmp_path / "run" / "ui", UI_KEY)))
    monkeypatch.chdir(tmp_path)
    create_app(Settings())


def test_the_deploy_env_example_starts_the_backend(tmp_path: Path, monkeypatch) -> None:
    """``spotdown.env`` from ``deploy/spotdown.env.example``, as compose.no-ui.yaml runs it."""
    _clean_env(monkeypatch)
    shutil.copy(ROOT / "deploy" / "spotdown.env.example", tmp_path / ".env")
    monkeypatch.setenv("SERVICE_KEY_FILE", str(_key(tmp_path / "run" / "service")))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    create_app(Settings())


@pytest.mark.parametrize("name", PATH_SETTINGS)
@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_path_setting_means_unset(monkeypatch, name: str, value: str) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(name, value)
    assert getattr(Settings(), name.lower()) is None


def test_an_empty_service_key_file_still_stops_the_start_up(tmp_path: Path, monkeypatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("SERVICE_KEY_FILE", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ServiceKeyError):
        create_app(Settings())


@pytest.mark.parametrize("name", ["SERVICE_KEY_FILE", "UI_SERVICE_KEY_FILE"])
def test_a_set_but_unreadable_key_file_still_stops_the_start_up(
    tmp_path: Path, monkeypatch, name: str
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("SERVICE_KEY_FILE", str(_key(tmp_path / "service")))
    monkeypatch.setenv(name, str(tmp_path / "missing"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ServiceKeyError):
        create_app(Settings())


def test_a_set_but_unreadable_spotify_secret_still_stops_the_start_up(
    tmp_path: Path, monkeypatch
) -> None:
    from app.service.spotify_client import SpotifyConfigError

    _clean_env(monkeypatch)
    monkeypatch.setenv("SERVICE_KEY_FILE", str(_key(tmp_path / "service")))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id-123")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(SpotifyConfigError):
        create_app(Settings())
