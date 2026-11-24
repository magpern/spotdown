"""The UI image in the release workflow (spotdown#78 scope 5): built and pushed as
``ghcr.io/magpern/spotdown-ui`` from the same version tag only, with the same action pinning,
tags, platforms, provenance, SBOM, attestation and digest note as the backend image."""

from __future__ import annotations

from typing import Any, Dict, List

import yaml

from .helpers import ROOT

RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _doc() -> Dict[Any, Any]:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


def _steps(job: str) -> List[Dict[str, Any]]:
    return _doc()["jobs"][job]["steps"]


def _uses(job: str) -> List[str]:
    return [s["uses"] for s in _steps(job) if "uses" in s]


def test_only_a_version_tag_runs_it() -> None:
    assert _doc()[True] == {"push": {"tags": ["v*.*.*"]}}
    assert _doc()["jobs"]["image-ui"]["needs"] == "verify"  # the SemVer-on-main check first
    for job in ("image-ui", "attest-image-ui"):
        assert "if" not in _doc()["jobs"][job], job  # no way around the verify job


def test_the_ui_image_name_and_its_build() -> None:
    assert _doc()["env"]["UI_IMAGE"] == "ghcr.io/magpern/spotdown-ui"
    ui = {s.get("id"): s for s in _steps("image-ui")}
    backend = {s.get("id"): s for s in _steps("image")}
    assert ui["meta"]["with"] == {**backend["meta"]["with"], "images": "${{ env.UI_IMAGE }}"}
    assert ui["build"]["with"] == {**backend["build"]["with"], "file": "Dockerfile.ui"}
    assert ui["build"]["uses"] == backend["build"]["uses"]
    assert _doc()["jobs"]["image-ui"]["outputs"] == {"digest": "${{ steps.build.outputs.digest }}"}


def test_the_same_pinned_actions_as_the_backend_image() -> None:
    assert _uses("image-ui") == _uses("image")
    assert _steps("image-ui")[0]["with"] == {"persist-credentials": False}


def test_the_ui_digest_is_attested_and_in_the_release_notes() -> None:
    attest = _steps("attest-image-ui")
    assert [s["uses"] for s in attest] == _uses("attest-image")
    assert attest[0]["with"] == {
        "subject-name": "${{ env.UI_IMAGE }}",
        "subject-digest": "${{ needs.image-ui.outputs.digest }}",
    }
    create = next(s for s in _steps("release") if s.get("name") == "Create the release")
    assert create["env"]["UI_DIGEST"] == "${{ needs.image-ui.outputs.digest }}"
    assert 'echo "- \\`$UI_IMAGE@$UI_DIGEST\\`"' in create["run"]
    assert 'echo "- \\`$IMAGE@$DIGEST\\`"' in create["run"]
