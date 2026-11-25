"""The runbook's steps match the deploy files (spotdown#79 acceptance 5): every release asset is
downloaded, every Compose variable is explained, every secret file is installed for uid 10001 and
mounted where the runbook says, and the fixed settings it names are the deploy file's."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set

import yaml

from .test_image_files import NO_UI, SPOTIFY, _overlay

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = (ROOT / "docs" / "runbooks" / "docker-image.md").read_text(encoding="utf-8")
DEPLOY_FILES = [ROOT / "deploy" / "compose.yaml", NO_UI, SPOTIFY]


def _assets() -> Set[str]:
    out = subprocess.run(
        ["bash", str(ROOT / "scripts" / "release-assets.sh"), "list"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return {line.split(" ", 1)[1] for line in out.splitlines()}


def _secret_files() -> Dict[str, str]:
    """Each secret name and its default file on the host, across the deploy files."""
    out: Dict[str, str] = {}
    for path in DEPLOY_FILES:
        for name, spec in (_overlay(path).get("secrets") or {}).items():
            match = re.fullmatch(r"\$\{[A-Z_]+:-(\./secrets/[a-z_]+)\}", spec["file"])
            assert match, (path.name, name, spec)
            out[name] = match.group(1)
    return out


def test_every_release_asset_is_downloaded_and_checked() -> None:
    download = re.search(r"gh release download .*?SHA256SUMS", RUNBOOK, re.S)
    assert download, "no gh release download line"
    named = set(re.findall(r"-p (\S+)", download.group(0)))
    assert named == _assets() | {"SHA256SUMS"}
    assert "sha256sum -c SHA256SUMS" in RUNBOOK


def test_every_compose_variable_is_in_the_table() -> None:
    used: Set[str] = set()
    for path in DEPLOY_FILES:
        used |= set(re.findall(r"\$\{([A-Z_]+)", path.read_text(encoding="utf-8")))
    table = set(re.findall(r"^\| `([A-Z_]+)` \|", RUNBOOK, re.M))
    assert used <= table, used - table


def test_every_secret_file_is_installed_for_the_images_user_and_mounted_as_described() -> None:
    files = _secret_files()
    assert set(files) == {
        "spotdown_service_key",
        "spotdown_ui_backend_key",
        "spotdown_spotify_client_secret",
    }
    installs = re.findall(r"install -m 0400 -o 10001 -g 10001 \S+ (\S+)", RUNBOOK)
    installed = {i.replace('"secrets/$name"', "") for i in installs}
    assert "secrets/spotdown_spotify_client_secret" in installed
    loop = re.search(r"for name in ([a-z_ ]+); do", RUNBOOK)
    assert loop, "no key loop"
    assert set(loop.group(1).split()) == {"spotdown_service_key", "spotdown_ui_backend_key"}
    for host_file in files.values():
        assert f"Default `{host_file}`" in RUNBOOK, host_file
    rows = dict(re.findall(r"^\| `([a-z_]+)`(?: \(optional\))? \| (.+?) \|$", RUNBOOK, re.M))
    mounts: Dict[str, Set[str]] = {}
    for path in DEPLOY_FILES:
        for svc, spec in _overlay(path)["services"].items():
            secrets = spec.get("secrets") or []
            if isinstance(secrets, tuple):  # !override in compose.no-ui.yaml: a subset
                continue
            for secret in secrets:
                mounts.setdefault(secret, set()).add(svc)
    for secret, where in mounts.items():
        assert set(re.findall(r"the (api|ui|worker)", rows[secret])) == where, secret


def test_the_fixed_settings_named_are_the_deploy_files() -> None:
    services = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    for svc in ("api", "ui"):
        for name, value in services["services"][svc]["environment"].items():
            assert f"`{name}={value}`" in RUNBOOK, (svc, name)
    spotify = _overlay(SPOTIFY)["services"]["api"]["environment"]
    for name, value in spotify.items():
        assert f"`{name}={value}`" in RUNBOOK, name


def test_the_overlays_and_the_bind_address_are_documented() -> None:
    assert "docker compose -f compose.yaml -f compose.no-ui.yaml up -d --wait" in RUNBOOK
    assert "docker compose -f compose.yaml -f compose.spotify.yaml up -d --wait" in RUNBOOK
    assert "SPOTDOWN_PUBLISH_HOST=<LAN address>" in RUNBOOK
    assert "### Upgrading from a SERVICE_MODE compose file" in RUNBOOK
    # The LAN address is a placeholder: no private address is written down as the owner's.
    assert not re.search(r"\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+", RUNBOOK)


def _section(heading: str) -> str:
    start = RUNBOOK.index(heading)
    end = RUNBOOK.find("\n## ", start)
    return RUNBOOK[start:end]


def test_the_ui_egress_note_names_the_one_backend_and_a_rule_that_cannot_lock_out() -> None:
    """spotdown#96: the UI's only destination is the backend's origin, and the firewall example
    matches only the UI's bridge, lets replies pass before it drops, and changes no policy."""
    note = _section("### The UI's outbound access")
    doc = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    ui = doc["services"]["ui"]
    assert f"`{ui['environment']['SPOTDOWN_BACKEND_URL']}`" in note
    assert "ui-published" in ui["networks"] and "ui-published" in doc["networks"]
    assert f"{doc['name']}_ui-published" in note  # the network's name as Compose creates it
    assert "the operator's choice" in note
    for topic in ("SPOTDOWN_PUBLISH_HOST", "ip6tables", "nftables"):
        assert topic in note, topic

    rules = re.findall(r"^sudo iptables (-\w) (.*)$", note, re.M)
    assert rules, "no iptables rule"
    chains: Dict[str, List[str]] = {}
    for flag, rest in rules:
        assert flag in ("-I", "-S"), (flag, rest)  # never -P, -F, -X, -A or -D in the block
        if flag == "-S":
            continue
        chain, pos, rule = rest.split(" ", 2)
        assert re.search(r'-[io] "\$UI_BR"', rule), rule  # only the UI's bridge is matched
        assert "ACCEPT" not in rule, rule
        chain_rules = chains.setdefault(chain, [])
        assert 1 <= int(pos) <= len(chain_rules) + 1, rest  # inserted in the order shown
        chain_rules.insert(int(pos) - 1, rule)
    user = chains["DOCKER-USER"]
    replies = [i for i, r in enumerate(user) if "ESTABLISHED,RELATED -j RETURN" in r]
    drops = [i for i, r in enumerate(user) if r.startswith('-i "$UI_BR"') and "-j DROP" in r]
    assert replies and drops and max(replies) < min(drops), user
    for rule in chains.get("INPUT", []):
        assert "--ctstate NEW" in rule, rule  # replies to the published port still reach it
