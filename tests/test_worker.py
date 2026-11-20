"""The worker side of a job: outcomes, the sandbox's rlimits, no audio left, the worker survives."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import numpy as np
import pytest
import soundfile as sf

from app.config import Settings
from app.service import url_fetch
from app.service.failure_policy import JobFailure, Reason
from app.service.routes import job_view
from app.service.store import Store
from app.service.uploads import upload_path
from app.service.worker import execute_job

from .service_helpers import (
    SCOPE,
    assert_valid,
    audio_files,
    fake_analyser_result,
    service_settings,
    write_wav,
)

SECRET_URL = "https://media.example.com/a/track.wav?X-Signature=s3cr3t-token"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = service_settings(tmp_path)
    s.ensure_dirs()
    return s


@pytest.fixture()
def store(settings: Settings) -> Store:
    return Store(settings.service_db_path)


def upload_job(settings: Settings, store: Store, audio: Path, job_id: str = "job_u1") -> str:
    upload_id = "up_" + job_id
    data = audio.read_bytes()
    upload_path(settings, upload_id).write_bytes(data)
    store.add_upload(upload_id, SCOPE, "a" * 64, len(data), "wav", 3600)
    assert store.claim_upload(upload_id, SCOPE) is not None
    store.add_job(
        {"job_id": job_id, "cache_scope": SCOPE, "source_kind": "upload", "upload_id": upload_id}
    )
    return job_id


def url_job(store: Store, job_id: str = "job_url1", url: str = SECRET_URL) -> str:
    store.add_job(
        {
            "job_id": job_id,
            "cache_scope": SCOPE,
            "client_ref": "t1",
            "source_kind": "url",
            "source_ref": "https://media.example.com/a/track.wav",
            "fetch_url": url,
        }
    )
    return job_id


def view(store: Store, job_id: str) -> Dict[str, Any]:
    job = store.get_job(job_id)
    assert job is not None
    out = job_view(job)
    assert_valid("job-v1", out)
    return out


def assert_no_audio_left(settings: Settings, store: Store, job_id: str) -> None:
    assert audio_files(settings) == []
    assert not (settings.work_dir / job_id).exists()
    job = store.get_job(job_id)
    assert job is not None and job["fetch_url"] is None
    if job["upload_id"]:
        assert store.get_upload(job["upload_id"]) is None


def fake_ok(audio: Path, media: str, work: Path, limits) -> Dict[str, Any]:
    (work / "decoded.tmp").write_bytes(b"\x00" * 64)  # the sandbox's scratch
    return fake_analyser_result()


def test_an_upload_job_ends_done_with_a_valid_analysis(settings, store, tmp_path) -> None:
    job_id = upload_job(settings, store, write_wav(tmp_path / "a.wav", 2))
    execute_job(job_id, settings, store, analyser=fake_ok)
    out = view(store, job_id)
    assert out["status"] == "done" and out["outcome"] is None
    doc = store.get_analysis(out["analysis_id"])
    assert_valid("analysis-v1", doc)
    assert_no_audio_left(settings, store, job_id)


@pytest.mark.parametrize(
    "reason,outcome,kind",
    [
        (Reason.DECODE, "processing_failed", "decode_error"),
        (Reason.TOO_LONG, "processing_failed", "out_of_memory"),
        (Reason.MEMORY, "processing_failed", "out_of_memory"),
        (Reason.CPU_LIMIT, "processing_failed", "timeout"),
        (Reason.DEADLINE, "processing_failed", "timeout"),
        (Reason.ANALYSIS, "processing_failed", "analysis_error"),
    ],
)
def test_a_failed_job_leaves_no_audio(settings, store, tmp_path, reason, outcome, kind) -> None:
    job_id = upload_job(settings, store, write_wav(tmp_path / "a.wav", 1))

    def failing(audio: Path, media: str, work: Path, limits) -> Dict[str, Any]:
        (work / "partial.tmp").write_bytes(b"\x00")
        raise JobFailure(reason)

    execute_job(job_id, settings, store, analyser=failing)
    out = view(store, job_id)
    assert (out["status"], out["outcome"], out["failure_kind"]) == ("failed", outcome, kind)
    assert "analysis_id" not in out
    assert_no_audio_left(settings, store, job_id)


def test_an_unexpected_error_fails_the_job_not_the_worker(settings, store, tmp_path, caplog):
    job_id = upload_job(settings, store, write_wav(tmp_path / "a.wav", 1))

    def broken(audio: Path, media: str, work: Path, limits) -> Dict[str, Any]:
        raise RuntimeError("boom at https://media.example.com/?sig=s3cr3t-token")

    execute_job(job_id, settings, store, analyser=broken)  # does not raise
    out = view(store, job_id)
    assert (out["outcome"], out["failure_kind"]) == ("processing_failed", "analysis_error")
    assert "s3cr3t" not in caplog.text
    assert_no_audio_left(settings, store, job_id)


def test_a_missing_upload_is_source_unavailable(settings, store, tmp_path) -> None:
    job_id = upload_job(settings, store, write_wav(tmp_path / "a.wav", 1))
    upload_path(settings, "up_" + job_id).unlink()
    execute_job(job_id, settings, store, analyser=fake_ok)
    assert view(store, job_id)["outcome"] == "source_unavailable"


def test_a_job_runs_once(settings, store, tmp_path) -> None:
    job_id = upload_job(settings, store, write_wav(tmp_path / "a.wav", 1))
    calls = []

    def counting(*args: Any) -> Dict[str, Any]:
        calls.append(1)
        return fake_analyser_result()

    execute_job(job_id, settings, store, analyser=counting)
    execute_job(job_id, settings, store, analyser=counting)
    assert calls == [1]


# ---- url source ----


def fake_fetcher(wav: Path):
    def fetch(url: str, dest: Path, **kwargs: Any) -> url_fetch.Fetched:
        assert url == SECRET_URL
        dest.write_bytes(wav.read_bytes())
        return url_fetch.Fetched(
            path=dest, sha256="b" * 64, bytes=1, ref="https://media.example.com/a/track.wav"
        )

    return fetch


def test_a_url_job_ends_done_and_forgets_the_url(settings, store, tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    job_id = url_job(store)
    wav = write_wav(tmp_path / "a.wav", 1)
    execute_job(job_id, settings, store, fetcher=fake_fetcher(wav), analyser=fake_ok)
    out = view(store, job_id)
    assert out["status"] == "done"
    doc = store.get_analysis(out["analysis_id"])
    assert_valid("analysis-v1", doc)
    assert doc is not None
    assert doc["provenance"]["source"] == {
        "kind": "url",
        "ref": "https://media.example.com/a/track.wav",
    }
    assert "fetch" in doc["provenance"]["run"]["timings_s"]
    assert "s3cr3t" not in str(store.get_job(job_id)) and "s3cr3t" not in caplog.text
    assert_no_audio_left(settings, store, job_id)


def real_fetcher(handler, addresses=("93.184.216.34",)):
    return functools.partial(
        url_fetch.fetch_url,
        resolver=lambda host: list(addresses),
        transport=httpx.MockTransport(handler),
    )


def _not_reached(*args: Any) -> Dict[str, Any]:
    raise AssertionError("nothing may be analysed")


@pytest.mark.parametrize(
    "handler,addresses,outcome,kind",
    [
        # Over the cap (settings: 2 MiB).
        (
            lambda r: httpx.Response(200, stream=httpx.ByteStream(b"RIFF" + b"\x00" * (3 << 20))),
            ("93.184.216.34",),
            "processing_failed",
            "out_of_memory",
        ),
        (
            lambda r: httpx.Response(302, headers={"location": "https://other.example.com/x.wav"}),
            ("93.184.216.34",),
            "source_unavailable",
            None,
        ),
        (lambda r: httpx.Response(200), ("10.0.0.7",), "source_unavailable", None),
        (lambda r: httpx.Response(200), ("127.0.0.1",), "source_unavailable", None),
        (lambda r: httpx.Response(200), ("::1",), "source_unavailable", None),
        (lambda r: httpx.Response(404), ("93.184.216.34",), "source_unavailable", None),
        (lambda r: httpx.Response(502), ("93.184.216.34",), "provider_error", None),
        (
            lambda r: httpx.Response(200, stream=httpx.ByteStream(b"<html>not audio</html>")),
            ("93.184.216.34",),
            "processing_failed",
            "decode_error",
        ),
    ],
)
def test_url_job_failures(settings, store, handler, addresses, outcome, kind) -> None:
    job_id = url_job(store)
    execute_job(
        job_id, settings, store, fetcher=real_fetcher(handler, addresses), analyser=_not_reached
    )
    out = view(store, job_id)
    assert (out["status"], out["outcome"], out["failure_kind"]) == ("failed", outcome, kind)
    assert_no_audio_left(settings, store, job_id)


def test_a_rate_limited_url_job_carries_retry_after(settings, store) -> None:
    job_id = url_job(store)
    handler = lambda r: httpx.Response(429, headers={"retry-after": "42"})  # noqa: E731
    execute_job(job_id, settings, store, fetcher=real_fetcher(handler), analyser=_not_reached)
    out = view(store, job_id)
    assert out["outcome"] == "rate_limited" and out["retry_after_s"] == 42.0


# ---- the real sandbox: rlimits on decoded length, memory, CPU time and wall time ----


def noise_wav(path: Path, seconds: int, sr: int = 8000) -> Path:
    y = (np.random.default_rng(1).standard_normal(sr * seconds) * 0.2).astype(np.float32)
    sf.write(str(path), y, sr, subtype="PCM_16")
    return path


def _run_real(
    settings: Settings, store: Store, audio: Path, job_id: str, **limits: Any
) -> Dict[str, Any]:
    for name, value in limits.items():
        setattr(settings, name, value)
    upload_job(settings, store, audio, job_id)
    execute_job(job_id, settings, store)
    return view(store, job_id)


@pytest.mark.parametrize(
    "seconds,limits,kind",
    [
        # Overlong: decoded length over the cap (refused after decoding at most the cap + 1 s).
        (8, {"analysis_max_decoded_s": 5.0}, "out_of_memory"),
        # Oversized: ten minutes of audio does not fit in a 1 GiB address space.
        (600, {"analysis_max_memory_mb": 1024}, "out_of_memory"),
        # CPU time: the child is stopped by RLIMIT_CPU.
        (8, {"analysis_max_cpu_s": 1}, "timeout"),
        # Wall time: the child is killed at the deadline.
        (8, {"analysis_wall_timeout_s": 1}, "timeout"),
    ],
)
def test_rlimits_end_as_processing_failed_and_the_worker_survives(
    settings, store, tmp_path, seconds, limits, kind
) -> None:
    audio = (
        noise_wav(tmp_path / "in.wav", seconds)
        if seconds > 60
        else write_wav(tmp_path / "in.wav", seconds)
    )
    out = _run_real(settings, store, audio, "job_limit", **limits)
    assert (out["status"], out["outcome"], out["failure_kind"]) == (
        "failed",
        "processing_failed",
        kind,
    )
    assert_no_audio_left(settings, store, "job_limit")
    # The same worker process goes on to the next job.
    nxt = upload_job(settings, store, write_wav(tmp_path / "next.wav", 1), "job_next")
    execute_job(nxt, settings, store, analyser=fake_ok)
    assert view(store, nxt)["status"] == "done"


def test_the_real_sandbox_analyses_fixture_audio(settings, store, tmp_path) -> None:
    out = _run_real(settings, store, write_wav(tmp_path / "in.wav", 10), "job_real")
    assert out["status"] == "done", out
    doc: Optional[Dict[str, Any]] = store.get_analysis(out["analysis_id"])
    assert_valid("analysis-v1", doc)
    assert doc is not None and doc["provenance"]["audio"]["codec"] == "pcm"
    assert_no_audio_left(settings, store, "job_real")


def test_the_worker_pins_the_http_client_loggers_to_warning() -> None:
    """httpx logs each request line (with its query string) at INFO; a url source's query can
    be a signature, so importing the worker (done above) pins those loggers to WARNING."""
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING
