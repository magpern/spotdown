"""Helpers for the UI image's tests (ADR 0005, spotdown#78): a faked backend behind an
``httpx.MockTransport`` that records every outbound request, and a UI app on a temporary data
directory with a client that signed in through the real ``POST /login``. Nothing touches the
network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spotdown_ui.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://localhost:8000"
UI_HEADERS = {"X-Spotdown-UI": "1", "Origin": BASE}
LOGIN_HEADERS = {**UI_HEADERS, "Content-Type": "application/json"}
BACKEND_URL = "http://spotdown-api:8000"
# Markup and URL-safe characters both, so a leak shows raw or escaped.
KEY = "uiKey-0123456789abcdefABCDEF-<b>&'\"-0123456789"
SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
VIDEO = "dQw4w9WgXcQ"
AUDIO_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64
LEAKY_HEADERS = {"x-backend-internal": "leak", "set-cookie": "backend=1", "server": "backend"}


def analysis_doc() -> Dict[str, Any]:
    path = ROOT / "schema" / "examples" / "analysis-v1.minimal.json"
    return json.loads(path.read_text(encoding="utf-8"))


class Seen(NamedTuple):
    method: str
    scheme: str
    host: str
    port: Optional[int]
    path: str  # raw, as sent
    query: str
    headers: Dict[str, str]
    body: bytes


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body, headers=LEAKY_HEADERS)


class FakeBackend:
    """The backend's ``/v2`` routes as ADR 0005 and #76/#77 state them, answered in memory."""

    def __init__(self) -> None:
        self.seen: List[Seen] = []
        self.overrides: Dict[tuple, httpx.Response] = {}
        self.transport = httpx.MockTransport(self.handle)

    def paths(self) -> List[tuple]:
        return [(s.method, s.path) for s in self.seen]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        url = request.url
        self.seen.append(
            Seen(
                request.method,
                url.scheme,
                url.host,
                url.port,
                url.raw_path.decode("ascii").split("?")[0],
                url.query.decode("ascii"),
                dict(request.headers),
                body,
            )  # fmt: skip
        )
        key = (request.method, url.path)
        if key in self.overrides:
            return self.overrides[key]
        return self.answer(request.method, url.path, body)

    def answer(self, method: str, path: str, body: bytes) -> httpx.Response:
        if (method, path) == ("POST", "/v2/spotify/resolve"):
            track = {"spotify_id": SPOTIFY_ID, "title": "Song <i>", "artists": ["Artist"],
                     "album": "Album", "duration_ms": 213000, "isrc": "USRC17607839"}  # fmt: skip
            return _json(200, {"tracks": [track], "truncated": False})
        if (method, path) == ("POST", "/v2/match"):
            best = {"source": f"youtube:{VIDEO}", "recording_match_confidence": 0.92,
                    "band": "confident", "duration_s": 213.8, "duration_delta_s": 0.8,
                    "duration_check": "ok"}  # fmt: skip
            result = {"schema_version": "match-v1", "client_ref": "0", "status": "matched",
                      "matcher_version": "youtube-1", "best": best, "alternates": []}  # fmt: skip
            return _json(200, {"results": [result]})
        if (method, path) == ("PUT", "/v2/uploads"):
            return _json(
                201, {"upload_id": "up_" + "a" * 32, "sha256": "b" * 64, "bytes": len(body)}
            )
        if (method, path) == ("POST", "/v2/analyses"):
            return _json(202, {"job_id": "job_1", "status": "queued"})
        if (method, path) == ("POST", "/v2/audio"):
            return _json(202, {"job_id": "job_a"})
        if (method, path) == ("GET", "/v2/jobs/job_1"):
            return _json(200, {"schema_version": "job-v1", "job_id": "job_1", "status": "done",
                               "client_ref": None, "outcome": None, "retry_after_s": None,
                               "analysis_id": "an_1", "failure_kind": None})  # fmt: skip
        if (method, path) == ("GET", "/v2/jobs/job_a"):
            return _json(200, {"job_id": "job_a", "status": "done", "audio_id": "au_1"})
        if (method, path) == ("GET", "/v2/analyses/an_1"):
            return _json(200, analysis_doc())
        if (method, path) == ("GET", "/v2/audio/au_1"):
            headers = {**LEAKY_HEADERS, "content-type": "audio/mpeg",
                       "content-disposition": 'attachment; filename="../../evil.sh"'}  # fmt: skip
            return httpx.Response(200, content=AUDIO_BYTES, headers=headers)
        return _json(404, {"error": {"code": "not_found", "message": "not found"}})


def ui_env(tmp_path: Path, key: str = KEY, **overrides: str) -> Dict[str, str]:
    key_file = tmp_path / "backend_key"
    key_file.write_text(key + "\n", encoding="utf-8")
    env = {
        "SPOTDOWN_BACKEND_URL": BACKEND_URL,
        "SPOTDOWN_BACKEND_KEY_FILE": str(key_file),
        "UI_DATA_DIR": str(tmp_path / "data"),
    }
    env.update(overrides)
    return env


class Ui:
    """A UI app on ``tmp_path`` with a faked backend, and its generated access token."""

    def __init__(self, tmp_path: Path, **overrides: str) -> None:
        self.env = ui_env(tmp_path, **overrides)
        self.backend = FakeBackend()
        self.app: FastAPI = create_app(self.env, self.backend.transport)

    @property
    def token(self) -> str:
        path = self.env.get("UI_ACCESS_TOKEN_FILE") or str(
            Path(self.env["UI_DATA_DIR"]) / "ui_token"
        )
        return Path(path).read_text(encoding="utf-8").strip()

    def anon(self, base_url: str = BASE) -> TestClient:
        return TestClient(self.app, base_url=base_url)

    def signed(self, base_url: str = BASE) -> TestClient:
        client = TestClient(self.app, base_url=base_url, headers=UI_HEADERS)
        res = client.post("/login", json={"token": self.token}, headers=LOGIN_HEADERS)
        assert res.status_code == 204, res.text
        client.headers["Cookie"] = res.headers["set-cookie"].split(";", 1)[0]
        return client
