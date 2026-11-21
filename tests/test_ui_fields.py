"""The standalone web UI: it is served, and every field it reads exists in the document the v1
analysis endpoint returns (a synthetic fixture; no network, no provider)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app import standalone
from app.analyze import analyze_track
from app.main import create_app
from app.quality.fixtures_io import render_to
from app.quality.specs import SPECS
from app.service import analysis_doc

from .service_helpers import assert_valid, canned_core
from .standalone_helpers import generated_token, signed_in, standalone_settings

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
    listed = set(fields["required"] + fields["optional"] + fields["report_only"])
    read = set()
    for js in STATIC.glob("analysis_*.js"):
        read |= set(re.findall(r'docAt\(\s*doc\s*,\s*"([^"]+)"', js.read_text(encoding="utf-8")))
    assert read and read <= listed, read - listed
    assert listed <= read, listed - read  # a listed field nobody reads is stale


def test_the_explicit_object_is_read_from_a_hand_made_document() -> None:
    """content.explicit (spotdown#36) is report-only and may not be on every base yet."""
    doc = {"content": {"explicit": {"value": "yes", "confidence": 0.9, "method": "lyrics"}}}
    for path in _fields()["DOC_FIELDS"]["report_only"]:
        assert _at(doc, path) is not MISSING, path
    js = (STATIC / "analysis_view.js").read_text(encoding="utf-8")
    assert "currentColor" in js and "Apache License 2.0" in js  # the inline icon and its licence


def _hand_doc() -> Dict[str, Any]:
    """A document with fades and silence (the fixture has none) and the explicit object as the
    schema example of spotdown#36 spells it (hand-made: that branch is not a dependency)."""
    core = canned_core(40)
    core["fade_in"] = [0.4, 3.0, 0.7]
    core["fade_out"] = [34.0, 39.0, 0.6]
    doc = analysis_doc.build(
        core=core,
        probe={"codec": "mp3", "sample_rate_hz": 22050, "channels": 1},
        components={},
        sha256="c" * 64,
        source={"kind": "upload"},
        job_id="j",
        analysis_id="an_h",
        timings_s={},
        created_at="2026-11-21T10:00:00Z",
    )
    doc["content"] = {
        "explicit": {
            "value": "yes",
            "confidence": 0.5,
            "method": "metadata",
            "evidence": [
                {"source": "caller", "value": False},
                {"source": "youtube_age_limit", "value": 18},
            ],
        }
    }
    return doc


def test_the_item_fields_the_page_reads_exist() -> None:
    items = _fields()["DOC_FIELDS"]["items"]
    read = set()
    for js in STATIC.glob("*.js"):
        read |= set(re.findall(r'\bit\(\s*\w+\s*,\s*"([^"]+)"\)', js.read_text(encoding="utf-8")))
    assert read == {k for keys in items.values() for k in keys}  # no unlisted read, none stale
    docs = [_result()["document"], _hand_doc()]
    for path, keys in items.items():
        values = [v for v in (_at(d, path) for d in docs) if v not in (MISSING, None, [])]
        assert values, f"no fixture holds {path}"
        value = values[0]
        # An optional key (a build-up's end_s) need only occur in one item of the list.
        found = {k for item in (value if isinstance(value, list) else [value]) for k in item}
        assert set(keys) <= found, (path, set(keys) - found)


def test_the_document_is_schema_valid_and_aligned() -> None:
    doc = _result()["document"]
    assert_valid("analysis-v1", doc)
    assert doc["alignment"]["reference"]["kind"] == "spotify"
    assert doc["provenance"]["alignment"]["result"] == "ok"


def _client(tmp_path: Path, monkeypatch, **overrides: Any) -> TestClient:
    settings = standalone_settings(tmp_path, **overrides)
    monkeypatch.setattr(standalone, "get_settings", lambda: settings)
    app = create_app(settings)
    return signed_in(app, generated_token(settings))


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


def test_the_hand_made_document_is_schema_valid() -> None:
    """The explicit object the page reads is the real schema's (spotdown#39), not a guess."""
    assert_valid("analysis-v1", _hand_doc())


def test_single_segment_routes_come_before_the_asset_route() -> None:
    paths = [getattr(r, "path", "") for r in standalone.router.routes]
    catch_all = paths.index("/{name}")
    for i, path in enumerate(paths):
        if re.fullmatch(r"/[^/{]+", path):
            assert i < catch_all, path


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_views_render_with_values_and_with_nulls() -> None:
    full = _result()
    hand = {"document": _hand_doc()}
    bare = json.loads(json.dumps(full))  # optional groups absent, explicit unknown, odd values
    for group in ("meter", "downbeats", "phrases"):
        bare["document"]["grid"].pop(group, None)
    bare["document"].pop("labels", None)
    bare["document"].pop("alignment", None)
    bare["document"]["provenance"]["alignment"] = {"result": "failed"}  # no hypotheses
    bare["document"]["content"] = {
        "explicit": {"value": "unknown", "confidence": "n/a", "method": "metadata", "evidence": []}
    }
    run = subprocess.run(
        ["node", str(Path(__file__).with_name("ui_harness.js")), str(STATIC)],
        input=json.dumps([full, hand, bare]),
        capture_output=True,
        text=True,
        check=True,
    )
    full_html, hand_html, bare_html = json.loads(run.stdout)
    for html in (full_html, hand_html, bare_html):
        assert not re.search(r"undefined|NaN|>null<|[^a-z]null[^a-z]", html), html[:300]
    assert "–0:00.0" not in full_html  # a drop has no end: it must not render as a range
    assert re.search(r"drop</td><td>\d+:\d\d\.\d</td>", full_html)  # a drop has no end time
    assert "<title>fade in" in hand_html and "<title>silence" in hand_html
    assert "<title>fade out" in hand_html and "Explicit" in hand_html
    assert "YouTube age limit: 18" in hand_html and "caller&#39;s explicit flag: false" in hand_html
    assert "explicit: unknown" in bare_html and "alignment failed" in bare_html
