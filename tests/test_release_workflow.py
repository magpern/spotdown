"""The release workflow (spotdown#66, docs/runbooks/docker-image.md): what triggers it, what it may
do, and what it publishes. Read from the file itself; nothing runs and nothing is pushed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows"
RELEASE = WORKFLOW / "release.yml"
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")


def _load(path: Path) -> Dict[Any, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _code_lines(path: Path) -> List[str]:
    """The lines with their comments dropped (a comment may say `latest`)."""
    return [
        line.split(" #", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def _jobs() -> Dict[str, Dict[str, Any]]:
    return _load(RELEASE)["jobs"]


def _steps(job: str) -> List[Dict[str, Any]]:
    return _jobs()[job]["steps"]


def _step(job: str, uses_prefix: str) -> Dict[str, Any]:
    found = [s for s in _steps(job) if str(s.get("uses", "")).startswith(uses_prefix)]
    assert len(found) == 1, (job, uses_prefix, found)
    return found[0]


def test_only_a_pushed_version_tag_runs_it() -> None:
    # PyYAML reads the bare key `on` as True.
    trigger = _load(RELEASE)[True]
    assert trigger == {"push": {"tags": ["v*.*.*"]}}


def test_the_tag_must_be_semver_and_on_main() -> None:
    verify = _jobs()["verify"]
    assert verify["if"] == "github.repository == 'magpern/spotdown'"
    run = "\n".join(s.get("run", "") for s in verify["steps"])
    semver = re.search(r"semver='([^']+)'", run)
    assert semver, run
    pattern = re.compile(semver.group(1))
    for good in ("v0.1.0", "v1.2.3", "v10.20.30", "v1.2.3-rc.1"):
        assert pattern.match(good), good
    for bad in ("1.2.3", "v1.2", "v01.2.3", "v1.2.3.4", "v1.2.3+meta", "vlatest", "v1.2.3-"):
        assert not pattern.match(bad), bad
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in run
    checkout = _step("verify", "actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    # Every later job waits for the check, directly or through another job.
    assert _jobs()["image"]["needs"] == "verify"
    assert _jobs()["attest-image"]["needs"] == "image"
    assert set(_jobs()["release"]["needs"]) == {"image", "attest-image"}


def test_permissions_are_minimal_per_job() -> None:
    assert _load(RELEASE)["permissions"] == {}
    want = {
        "verify": {"contents": "read"},
        "image": {"contents": "read", "packages": "write"},
        "attest-image": {"id-token": "write", "attestations": "write"},
        "release": {"contents": "write", "id-token": "write", "attestations": "write"},
    }
    assert {name: job.get("permissions") for name, job in _jobs().items()} == want


def test_every_action_in_every_workflow_is_pinned_by_commit_sha() -> None:
    for path in sorted(WORKFLOW.glob("*.yml")):
        for job in _load(path)["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert PINNED.match(step["uses"]), (path.name, step["uses"])


def test_no_checkout_keeps_credentials() -> None:
    for job in _jobs():
        for step in _steps(job):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, job


def test_the_image_tags_are_version_minor_and_sha_never_latest() -> None:
    assert _load(RELEASE)["env"]["IMAGE"] == "ghcr.io/magpern/spotdown"
    meta = _step("image", "docker/metadata-action@")["with"]
    assert meta["images"] == "${{ env.IMAGE }}"
    assert meta["flavor"] == "latest=false"
    tags = [t.strip() for t in meta["tags"].splitlines() if t.strip()]
    assert tags == [
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "type=sha,format=short",
    ]
    code = "\n".join(_code_lines(RELEASE)).replace("latest=false", "")
    assert "latest" not in code


def test_both_platforms_with_provenance_and_sbom_and_no_build_secrets() -> None:
    build = _step("image", "docker/build-push-action@")["with"]
    assert build["context"] == "."
    assert build["platforms"] == "linux/amd64,linux/arm64"
    assert build["push"] is True
    assert build["provenance"] == "mode=max"
    assert build["sbom"] is True
    for forbidden in ("secrets", "secret-files", "build-args", "cache-from", "cache-to", "file"):
        assert forbidden not in build, forbidden


def test_the_digest_and_every_asset_are_attested_before_the_release() -> None:
    attest = _step("attest-image", "actions/attest@")["with"]
    assert attest == {
        "subject-name": "${{ env.IMAGE }}",
        "subject-digest": "${{ needs.image.outputs.digest }}",
    }
    assert _jobs()["image"]["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    steps = _steps("release")
    names = [s.get("name") or s.get("uses", "") for s in steps]
    built = names.index("Build the release assets")
    attested = next(
        i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/attest@")
    )
    created = names.index("Create the release")
    assert built < attested < created
    assert steps[attested]["with"] == {"subject-path": "${{ runner.temp }}/release-assets/*"}
    assert "sha256sum -c SHA256SUMS" in steps[built]["run"]


def test_the_release_has_generated_notes_assets_and_prerelease_for_a_hyphen() -> None:
    run = next(s["run"] for s in _steps("release") if s.get("name") == "Create the release")
    assert 'if [[ "$TAG" == *-* ]]; then flags+=(--prerelease); fi' in run
    create = [line.strip() for line in run.splitlines() if "gh release create" in line]
    assert len(create) == 1
    for part in ('"$TAG"', "--generate-notes", "--verify-tag", '"$assets"/*'):
        assert part in create[0], part


def test_only_the_release_job_can_write_contents_and_only_image_can_push() -> None:
    for name, job in _jobs().items():
        perms = job.get("permissions", {})
        assert (perms.get("contents") == "write") == (name == "release"), name
        assert (perms.get("packages") == "write") == (name == "image"), name


EXPRESSION = re.compile(r"\$\{\{")


def _run_blocks(path: Path) -> List[tuple]:
    """``(job, step name, script)`` of every ``run:`` step of the workflow at ``path``."""
    found = []
    for job_name, job in _load(path)["jobs"].items():
        for i, step in enumerate(job.get("steps", [])):
            if "run" in step:
                found.append((job_name, step.get("name", i), str(step["run"])))
    return found


def test_no_run_block_interpolates_an_expression() -> None:
    """A ``${{ }}`` inside ``run:`` is pasted into the script before the shell sees it, so a tag
    name or any other input could inject commands. Values reach a script through ``env:`` only.
    Every workflow, the release one first."""
    paths = [RELEASE] + sorted(p for p in WORKFLOW.glob("*.y*ml") if p != RELEASE)
    assert _run_blocks(RELEASE), "release.yml has run steps to check"
    for path in paths:
        for job, step, script in _run_blocks(path):
            assert not EXPRESSION.search(script), (path.name, job, step)
