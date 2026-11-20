"""Shared helpers for the service-mode tests: settings, a client, fixture audio and fakes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.config import Settings
from app.main import create_app

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
TEST_KEY = "test-service-key-3f9a1c7e5b2d4086a1b2c3d4e5f60718"
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
ALLOWED_HOST = "media.example.com"
SCOPE = "scope_a"


def service_settings(tmp_path: Path, **overrides: Any) -> Settings:
    key_file = tmp_path / "service_key"
    key_file.write_text(TEST_KEY + "\n", encoding="utf-8")
    values: Dict[str, Any] = {
        "service_mode": True,
        "service_key_file": key_file,
        "data_dir": tmp_path / "data",
        "redis_url": "redis://127.0.0.1:1/0",
        "url_allowed_hosts": ALLOWED_HOST,
        "max_upload_bytes": 2 * 1024 * 1024,
        "analysis_wall_timeout_s": 300,
        "analysis_max_cpu_s": 240,
    }
    values.update(overrides)
    return Settings(**values)


class RecordingDispatch:
    def __init__(self, then: Optional[Callable[[str], None]] = None) -> None:
        self.job_ids: List[str] = []
        self.then = then

    def __call__(self, job_id: str) -> None:
        self.job_ids.append(job_id)
        if self.then is not None:
            self.then(job_id)


def client_for(settings: Settings, dispatch: Optional[Callable[[str], None]] = None) -> TestClient:
    return TestClient(create_app(settings, dispatch or RecordingDispatch()))


def click_track(seconds: float, sr: int = 22050, bpm: float = 120.0) -> np.ndarray:
    """A deterministic fixture: a quiet tone with kicks, louder in the second half."""
    t = np.arange(int(sr * seconds)) / sr
    y = 0.05 * np.sin(2 * np.pi * 220 * t)
    n = int(0.03 * sr)
    kick = np.exp(-np.arange(n) / 200.0) * np.sin(2 * np.pi * 60 * np.arange(n) / sr)
    beat = 60.0 / bpm
    for k in range(int(seconds / beat)):
        i = int(k * beat * sr)
        amp = 0.9 if k * beat > seconds / 2 else 0.3
        y[i : i + n] += amp * kick[: len(y[i : i + n])]
    return y.astype(np.float32)


def write_wav(path: Path, seconds: float, sr: int = 22050) -> Path:
    sf.write(str(path), click_track(seconds, sr), sr, subtype="PCM_16")
    return path


def wav_bytes(tmp_path: Path, seconds: float = 4.0) -> bytes:
    return write_wav(tmp_path / "fixture.wav", seconds).read_bytes()


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def assert_valid(name: str, doc: Any) -> None:
    errors = [e.message for e in validator(name).iter_errors(doc)]
    assert errors == [], errors


def audio_files(settings: Settings) -> List[Path]:
    """Every file left under the service's upload and work directories."""
    found: List[Path] = []
    for root in (settings.uploads_dir, settings.work_dir):
        if root.exists():
            found += [p for p in root.rglob("*") if p.is_file()]
    return found


def canned_core(seconds: int = 12) -> Dict[str, Any]:
    """A core-analyser result of the shape app.core.analyser returns, without running librosa."""
    energy = [round(0.2 + 0.6 * (i >= seconds // 2), 3) for i in range(seconds)]
    return {
        "duration_s": float(seconds),
        "content_start_s": 0.4,
        "content_end_s": seconds - 0.5,
        "bpm": 120.0,
        "beat_times": [round(0.5 * i + 0.4, 3) for i in range(2 * seconds - 1)],
        "downbeat_phase": 0,
        "bars": [],
        "drops": [float(seconds // 2)],
        "build_ups": [{"start": 2.0, "end": float(seconds // 2)}],
        "energy": {
            "t": [float(i) for i in range(seconds)],
            "full": energy,
            "percussive": energy,
            "low": energy,
            "struct": energy,
        },
        "segments": [
            {"start": 0.0, "end": float(seconds // 2), "group": 1, "label": "intro"},
            {"start": float(seconds // 2), "end": float(seconds), "group": 2, "label": "verse"},
        ],
    }


def fake_analyser_result() -> Dict[str, Any]:
    return {
        "core": canned_core(),
        "probe": {"codec": "pcm", "sample_rate_hz": 22050, "channels": 1, "bitrate_bps": 352800},
        "components": {"python": "3.11", "librosa": "0.10.2"},
        "timings_s": {"decode": 0.1, "analyse": 0.2},
    }
