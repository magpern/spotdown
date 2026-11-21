"""The standalone web UI: it is served, and every field it reads exists in the document the v1
analysis endpoint returns (a synthetic fixture; no network, no provider)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app import standalone
from app.analyze import analyze_track
from app.config import Settings
from app.main import create_app
from app.quality.fixtures_io import render_to
from app.quality.specs import SPECS

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
MISSING = object()


def _at(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return MISSING
        obj = obj[key]
    return obj


def _fields() -> Dict[str, Any]:
    text = (STATIC / "analysis_model.js").read_text(encoding="utf-8")
    block = text.split("/*fields:begin*/")[1].split("/*fields:end*/")[0]
    out = {}
    for name in ("DOC_FIELDS", "V1_FIELDS"):
        literal = re.search(rf"const {name} = (.*?);\n", block, re.S)
        assert literal, name
        out[name] = json.loads(re.sub(r",(\s*[\]}])", r"\1", literal.group(1)))
    return out


@lru_cache(maxsize=1)
def _result() -> Dict[str, Any]:
    import tempfile

    spec = next(s for s in SPECS if s["id"] == "pickup-edm-140")
    with tempfile.TemporaryDirectory() as tmp:
        path = render_to(spec, Path(tmp))
        import soundfile as sf

        seconds = round(sf.info(str(path)).duration)
        return analyze_track(path, "fixture-track", seconds)


def test_every_field_the_page_reads_is_in_the_v1_document() -> None:
    fields, result = _fields(), _result()
    document = result["document"]
    for path in fields["V1_FIELDS"]:
        assert _at(result, path) is not MISSING, path
    for path in fields["DOC_FIELDS"]["required"] + fields["DOC_FIELDS"]["optional"]:
        assert _at(document, path) is not MISSING, path


def test_the_scripts_read_only_listed_fields() -> None:
    fields = _fields()["DOC_FIELDS"]
    listed = set(fields["required"] + fields["optional"])
    read = set()
    for js in STATIC.glob("analysis_*.js"):
        read |= set(re.findall(r'docAt\(\s*doc\s*,\s*"([^"]+)"', js.read_text(encoding="utf-8")))
    assert read and read <= listed, read - listed
    assert listed <= read, listed - read  # a listed field nobody reads is stale


def test_the_document_is_schema_valid_and_aligned() -> None:
    from .service_helpers import assert_valid

    doc = _result()["document"]
    assert_valid("analysis-v1", doc)
    assert doc["alignment"]["reference"]["kind"] == "spotify"
    assert doc["provenance"]["alignment"]["result"] == "ok"


def _client(tmp_path: Path, monkeypatch, **overrides: Any) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", redis_url="redis://127.0.0.1:1/0", **overrides)
    settings.ensure_dirs()
    monkeypatch.setattr(standalone, "get_settings", lambda: settings)
    return TestClient(create_app(settings))


def test_the_ui_and_its_assets_are_served(tmp_path: Path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        page = client.get("/")
        assert page.status_code == 200 and "analysis_view.js" in page.text
        for name in standalone.UI_ASSETS:
            assert f'"{name}"' in page.text
            res = client.get(f"/{name}")
            assert res.status_code == 200 and res.content == (STATIC / name).read_bytes()
        assert client.get("/nope.js").status_code == 404
        assert client.get("/index.html").status_code == 404


def test_the_assets_are_not_served_with_the_ui_off(tmp_path: Path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch, serve_ui=False) as client:
        assert client.get("/ui.js").status_code == 404


@pytest.mark.parametrize("name", ["index.html", *sorted(standalone.UI_ASSETS)])
def test_each_ui_file_stays_small(name: str) -> None:
    assert len((STATIC / name).read_text(encoding="utf-8").splitlines()) < 300
