"""The runbook's steps match the deploy files (spotdown#79 acceptance 5): every release asset is
downloaded, every Compose variable is explained, every secret file is installed for uid 10001 and
mounted where the runbook says, and the fixed settings it names are the deploy file's."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest
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


_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.S | re.M)


def _blocks(note: str) -> List[str]:
    """The body of every fenced block, whatever its fence (``` or ~~~), info string or indent."""
    return [m.group(2) for m in _FENCE.finditer(note)]


# A firewall command (iptables, ip6tables, their -restore/-nft/-legacy variants, nft) and its first
# argument. _CMD finds one anywhere in a fenced block; _LINE finds one that starts a line outside
# the blocks, after an optional sudo. A mention inside a prose line is neither.
_TOOL = r"(ip6?tables(?:-[\w-]+)?|nft)\b[ \t]*(\S*)"
_CMD = re.compile(r"\b" + _TOOL)
_LINE = re.compile(r"^[ \t]*(?:sudo[ \t]+)?" + _TOOL, re.M)
_RULE = re.compile(r"^\s*(?:sudo\s+)?(ip6?tables)\s+(-\w)\s+(.*?)(?:\s*&&)?\s*$", re.M)


def test_the_ui_egress_note_names_the_one_backend_and_a_rule_that_cannot_lock_out() -> None:
    """spotdown#96: the UI's only destination is the backend's origin, and the firewall example
    matches only the UI's bridge, lets replies pass before it drops, and changes no policy."""
    note = _section("### The UI's outbound access")
    doc = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    ui = doc["services"]["ui"]
    assert f"`{ui['environment']['SPOTDOWN_BACKEND_URL']}`" in note
    assert "ui-published" in ui["networks"] and "ui-published" in doc["networks"]
    assert f"{doc['name']}_ui-published" in note  # the network's name as Compose creates it
    assert "<project>_ui-published" in note and "COMPOSE_PROJECT_NAME" in note  # spotdown#101 N5
    assert "the operator's choice" in note
    for topic in ("SPOTDOWN_PUBLISH_HOST", "ip6tables", "nftables", "firewall-cmd --reload"):
        assert topic in note, topic

    shell = "\n".join(_blocks(note))
    prose = _FENCE.sub("", note)  # the note outside its fenced blocks
    # spotdown#101 N3: never -P, -F, -X, -A or -D (or a long form), never a ruleset load (-restore,
    # nft), in any firewall command of any fenced block, or on any line that starts with one.
    commands = _CMD.findall(shell) + _LINE.findall(prose)
    assert commands, "no iptables command"
    for tool, flag in commands:
        assert tool in ("iptables", "ip6tables") and flag in ("-I", "-S", "-C"), (tool, flag)
    rules = _RULE.findall(shell) + _RULE.findall(prose)  # an unfenced command is checked too
    assert len(rules) == len(commands), (rules, commands)  # every command is parsed below
    chains: Dict[Tuple[str, str], List[str]] = {}
    for tool, flag, rest in rules:
        if flag in ("-S", "-C"):  # read-only
            continue
        chain, pos, rule = rest.split(" ", 2)
        assert re.search(r'-[io] "\$UI_BR"', rule), rule  # only the UI's bridge is matched
        assert "ACCEPT" not in rule, rule
        chain_rules = chains.setdefault((tool, chain), [])
        assert 1 <= int(pos) <= len(chain_rules) + 1, rest  # inserted in the order shown
        chain_rules.insert(int(pos) - 1, rule)
    user = chains[("iptables", "DOCKER-USER")]
    replies = [i for i, r in enumerate(user) if "ESTABLISHED,RELATED -j RETURN" in r]
    drops = [i for i, r in enumerate(user) if r.startswith('-i "$UI_BR"') and "-j DROP" in r]
    assert replies and drops and max(replies) < min(drops), user
    inputs = chains[("iptables", "INPUT")]
    assert inputs
    for rule in inputs:
        # spotdown#101 N2: exactly NEW, so the UI's replies to the port proxy still reach it.
        assert re.findall(r"--ctstate (\S+)", rule) == ["NEW"], rule
        assert rule.endswith("-j DROP"), rule


def test_the_ui_egress_note_states_the_internal_network_precondition() -> None:
    """spotdown#101 N1: the rules are enough only if the callers' network is internal. The note
    says so next to its "Why this is enough" claim, gives the check and what to do if it fails."""
    note = _section("### The UI's outbound access")
    claim = note[note.index("Why this is enough") : note.index("What it does not cover")]
    assert "as long as the precondition above holds" in claim
    assert "the callers' network, is internal" in claim
    pre = note[note.index("**Precondition: every other network the UI is on is internal.**") :]
    pre = pre[: pre.index("Why this is enough")]
    check = "docker network inspect -f '{{.Internal}}' \"${SPOTDOWN_SERVICE_NETWORK:-spotdown}\""
    assert check in pre and "must print true" in pre
    assert "these rules are not enough" in pre  # and what to do then:
    assert "docker network create --internal <name>" in pre
    assert "set `SPOTDOWN_SERVICE_NETWORK` to it" in " ".join(pre.split())


_DOCKER = r"""#!/bin/bash
case "$1 $2" in
  "compose ps") [ -n "$SPOTDOWN_VERSION" ] || exit 1; echo "$STUB_CT" ;;
  "inspect -f") [ "$4" = "$STUB_CT" ] && [ -n "$4" ] || exit 1; echo "$STUB_NETS " ;;
  "network inspect")
    [[ " $STUB_NETS " == *" $5 "* ]] || exit 1
    case "$4" in
      '{{.Internal}}') [[ " $STUB_INTERNAL " == *" $5 "* ]] && echo true || echo false ;;
      '{{.EnableIPv6}}')
        [ "$STUB_IPV6" = unknown ] && exit 1
        [[ " $STUB_IPV6 " == *" $5 "* ]] && echo true || echo false ;;
      '{{.Id}}') echo "${STUB_ID}0123456789" ;;
      *) exit 1 ;;
    esac ;;
  *) exit 1 ;;
esac
"""
_IP = '#!/bin/bash\n[ "$1 $2" = "link show" ] && [ "$3" = "$STUB_BRIDGE" ]\n'
_SUDO = r"""#!/bin/bash
[ "$1" = iptables ] || exit 1
shift
if [ "$1" = -S ]; then exit 0; fi
if [ "$1" = -C ]; then
  c=$2; shift 2
  while read -r l; do
    [[ "$l" =~ ^-I\ $c\ [0-9]+\ (.*)$ ]] && [ "${BASH_REMATCH[1]}" = "$*" ] && exit 0
  done < "$STUB_LOG"
  exit 1
fi
[[ "$*" == *"$STUB_SUDO_FAIL"* ]] && [ -n "$STUB_SUDO_FAIL" ] && exit 1
echo "$*" >> "$STUB_LOG"
"""
_OK = {
    "STUB_CT": "c0ffee",
    "STUB_NETS": "spotdown spotdown_ui-published",
    "STUB_INTERNAL": "spotdown",
    "STUB_ID": "0123456789ab",
    "STUB_BRIDGE": "br-0123456789ab",
    "STUB_SUDO_FAIL": "",
    "STUB_IPV6": "",
}
_INSERTS = [
    "-I DOCKER-USER 1 -i br-0123456789ab -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
    "-I DOCKER-USER 2 -i br-0123456789ab ! -o br-0123456789ab -j DROP",
    "-I INPUT 1 -i br-0123456789ab -m conntrack --ctstate NEW -j DROP",
]


def _run_block(tmp_path: Path, script: str, **stub: Optional[str]) -> Tuple[List[str], str]:
    """Run a runbook shell block in bash with docker, ip and sudo faked: no Docker, no firewall,
    no network. Returns the iptables changes it made and its stderr."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", _DOCKER), ("ip", _IP), ("sudo", _SUDO)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "iptables.log"
    log.write_text("", encoding="utf-8")
    given: Dict[str, Optional[str]] = {"V": "1.2.3", **_OK, **stub}
    env = {k: v for k, v in given.items() if v is not None}
    env.update(PATH=f"{bin_dir}:/usr/bin:/bin", STUB_LOG=str(log))
    out = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return log.read_text(encoding="utf-8").splitlines(), out.stderr


def _egress_blocks() -> Tuple[str, str]:
    blocks = _blocks(_section("### The UI's outbound access"))
    main = [b for b in blocks if "ui_bridge()" in b]
    lan = [b for b in blocks if "DOCKER-USER 3" in b]
    assert len(main) == 1 and len(lan) == 1, blocks
    return main[0], lan[0].replace("<proxy address>", "192.0.2.20")


@pytest.mark.parametrize("publish_host", ["", "127.0.0.1", "192.0.2.10"])
def test_the_egress_block_inserts_its_rules_when_every_check_passes(
    tmp_path: Path, publish_host: str
) -> None:
    """The same rules for the loopback bind and the LAN bind (spotdown#101 N4 and AC 3)."""
    main, _ = _egress_blocks()
    inserts, err = _run_block(tmp_path, main, SPOTDOWN_PUBLISH_HOST=publish_host)
    assert inserts == _INSERTS, err
    assert "STOP" not in err


def test_the_egress_block_finds_the_network_of_another_project_name(tmp_path: Path) -> None:
    main, _ = _egress_blocks()
    nets = {"STUB_NETS": "callers myproj_ui-published", "STUB_INTERNAL": "callers"}
    inserts, err = _run_block(tmp_path, main, **nets)
    assert inserts == _INSERTS, err


@pytest.mark.parametrize(
    "stub",
    [
        {"STUB_INTERNAL": ""},  # the callers' network is not internal (N1)
        {"STUB_NETS": "spotdown spotdown_ui-published bridge"},  # a third, open network
        {"STUB_CT": ""},  # the ui is not running
        {"V": None},  # V unset: Compose refuses
        {"STUB_NETS": "spotdown"},  # no ui-published network
        {"STUB_BRIDGE": "br-ffffffffffff"},  # the bridge is not named as expected (N4)
        {"STUB_IPV6": "spotdown_ui-published"},  # IPv6 on ui-published: not closed by iptables
        {"STUB_IPV6": "unknown"},  # the IPv6 setting cannot be read: not taken as off
        {"STUB_NETS": "spotdown spotdown_ui-published other_ui-published"},  # two, one left open
        {"STUB_NETS": "spotdown_ui-published bridge"},  # an open network after ui-published
    ],
    ids=[
        "service-not-internal",
        "open-network",
        "no-ui",
        "no-version",
        "no-network",
        "no-bridge",
        "ipv6",
        "ipv6-unknown",
        "two-ui-published",
        "open-network-last",
    ],
)
def test_the_egress_block_stops_loudly_and_inserts_nothing_when_a_check_fails(
    tmp_path: Path, stub: Dict[str, Optional[str]]
) -> None:
    """spotdown#101 N1 and N4: pasted whole, a failed check never yields rules that match nothing,
    or rules that leave a second route open, without saying so."""
    main, _ = _egress_blocks()
    inserts, err = _run_block(tmp_path, main + '\necho "UI_BR=[$UI_BR]" >&2', **stub)
    assert inserts == [], inserts
    assert "STOP:" in err and "UI_BR=[]" in err, err  # and the LAN rule's -C finds nothing


def test_the_egress_block_stops_after_a_failed_insert(tmp_path: Path) -> None:
    main, _ = _egress_blocks()
    inserts, _ = _run_block(tmp_path, main, STUB_SUDO_FAIL="DOCKER-USER 1")
    assert inserts == []  # the DROP is never inserted without the RETURN before it


def test_the_lan_rule_needs_the_block_before_it(tmp_path: Path) -> None:
    main, lan = _egress_blocks()
    inserts, err = _run_block(tmp_path, main + "\n" + lan)
    lan_rule = "-I DOCKER-USER 3 -o br-0123456789ab -m conntrack --ctstate NEW ! -s 192.0.2.20"
    lan_rule += " -j DROP"
    assert inserts == [*_INSERTS, lan_rule], err
    inserts, _ = _run_block(tmp_path, lan)  # in a new shell, without the block: nothing
    assert inserts == []
