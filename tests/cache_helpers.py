"""A service stack with a counting fake analyser, for the cache and idempotency tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi.testclient import TestClient

from app.config import Settings
from app.service import url_fetch
from app.service.store import Store
from app.service.worker import execute_job

from .service_helpers import (
    AUTH,
    SCOPE,
    RecordingDispatch,
    client_for,
    fake_analyser_result,
    service_settings,
    write_wav,
)

OCTET = {**AUTH, "Content-Type": "application/octet-stream"}
URL = "https://media.example.com/a/b.wav"


class Stack:
    """Settings, store, client and a worker that runs jobs inline with a counting fake."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.tmp = tmp_path
        self.settings: Settings = service_settings(tmp_path, **overrides)
        self.settings.ensure_dirs()
        self.store = Store(self.settings.service_db_path)
        self.analysed: List[str] = []  # the sha256 of every file the analyser was run on
        self.fetch_bytes: Optional[bytes] = None
        self.fingerprint_energy: Optional[List[float]] = None
        self.dispatch = RecordingDispatch(then=self._run)
        self.client: TestClient = client_for(self.settings, self.dispatch)

    # fakes
    def _analyser(self, audio: Path, media: str, work: Path, limits: Any) -> Dict[str, Any]:
        self.analysed.append(hashlib.sha256(audio.read_bytes()).hexdigest())
        result = fake_analyser_result()
        if self.fingerprint_energy is not None:
            result["core"]["energy"]["full"] = list(self.fingerprint_energy)
        return result

    def _fetcher(self, url: str, dest: Path, **_: Any) -> url_fetch.Fetched:
        assert self.fetch_bytes is not None
        dest.write_bytes(self.fetch_bytes)
        sha = hashlib.sha256(self.fetch_bytes).hexdigest()
        return url_fetch.Fetched(dest, sha, len(self.fetch_bytes), URL)

    def _run(self, job_id: str) -> None:
        execute_job(
            job_id, self.settings, self.store, fetcher=self._fetcher, analyser=self._analyser
        )

    # calls
    def audio(self, name: str, seconds: float = 2.0) -> bytes:
        return write_wav(self.tmp / name, seconds).read_bytes()

    def upload(self, data: bytes, scope: str = SCOPE, key: Optional[str] = None) -> Any:
        headers = dict(OCTET)
        if key:
            headers["Idempotency-Key"] = key
        return self.client.put(f"/v2/uploads?cache_scope={scope}", headers=headers, content=data)

    def analyse(self, upload_id: str, scope: str = SCOPE, key: Optional[str] = None) -> Any:
        return self.post({"cache_scope": scope, "upload_id": upload_id}, key)

    def post(self, body: Dict[str, Any], key: Optional[str] = None) -> Any:
        headers = dict(AUTH)
        if key:
            headers["Idempotency-Key"] = key
        return self.client.post("/v2/analyses", headers=headers, json=body)

    def analyse_bytes(self, data: bytes, scope: str = SCOPE) -> Any:
        """Upload then analyse; the analyse response."""
        return self.analyse(self.upload(data, scope).json()["upload_id"], scope)

    def analysis_id(self, res: Any, scope: str = SCOPE) -> str:
        job = self.client.get(f"/v2/jobs/{res.json()['job_id']}?cache_scope={scope}", headers=AUTH)
        assert job.json()["status"] == "done", job.json()
        return job.json()["analysis_id"]

    def lookup(self, scope: Optional[str] = SCOPE, **query: str) -> Any:
        if scope is not None:
            query["cache_scope"] = scope
        return self.client.get("/v2/analyses/lookup", headers=AUTH, params=query)

    def get_analysis(self, analysis_id: str, scope: Optional[str] = SCOPE) -> Any:
        params = {"cache_scope": scope} if scope else {}
        return self.client.get(f"/v2/analyses/{analysis_id}", headers=AUTH, params=params)

    def rows(self, table: str, scope_column: str = "cache_scope", scope: str = SCOPE) -> List:
        with self.store._conn() as conn:
            return conn.execute(
                f"SELECT * FROM {table} WHERE {scope_column} = ?", (scope,)
            ).fetchall()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


Fn = Callable[..., Any]
