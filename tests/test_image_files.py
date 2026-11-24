"""The production image and its deploy file (spotdown#66, docs/runbooks/docker-image.md): the
Dockerfile, ``.dockerignore`` and ``deploy/compose.yaml``. Read from the files; nothing is built
or started (the PR records a local build and an end-to-end run of the deploy file)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DEPLOY = ROOT / "deploy" / "compose.yaml"


def _instructions() -> List[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    out: List[str] = []
    buf = ""
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if not buf and (not line.strip() or line.lstrip().startswith("#")):
            continue
        buf += line.rstrip("\\").strip() + " "
        if not line.rstrip().endswith("\\"):
            out.append(buf.strip())
            buf = ""
    return out


def _named(keyword: str) -> List[str]:
    return [i[len(keyword) + 1 :] for i in _instructions() if i.split(" ", 1)[0] == keyword]


def _env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for env in _named("ENV"):
        for name, value in re.findall(r"([A-Z_]+)=(\S+)", env):
            out[name] = value
    return out


def _deploy() -> Dict[str, Any]:
    return yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))


def test_the_base_is_pinned_by_version_and_digest() -> None:
    froms = _named("FROM")
    assert len(froms) == 1, froms
    assert re.fullmatch(r"python:3\.11\.\d+-slim-[a-z]+@sha256:[0-9a-f]{64}", froms[0]), froms


def test_the_image_runs_as_the_fixed_unprivileged_user() -> None:
    users = _named("USER")
    assert users == ["10001:10001"], users
    # USER comes after every RUN, so nothing at run time is root.
    instructions = _instructions()
    last_run = max(i for i, x in enumerate(instructions) if x.startswith("RUN "))
    assert instructions.index("USER 10001:10001") > last_run


def test_the_image_copies_only_requirements_and_the_app() -> None:
    assert _named("COPY") == ["requirements.txt .", "app ./app"]
    assert not _named("ADD") and not _named("ARG")


def test_service_mode_is_the_default_and_data_lives_in_one_place() -> None:
    env = _env()
    assert env["SERVICE_MODE"] == "true"
    assert env["SERVE_UI"] == "false"
    assert env["SERVICE_KEY_FILE"] == "/run/secrets/spotdown_service_key"
    assert env["DATA_DIR"] == "/data"
    assert env["NUMBA_CACHE_DIR"].startswith("/tmp/")
    for name in env:
        assert not re.search(r"KEY$|SECRET|TOKEN|PASSWORD", name), name


def test_ffmpeg_and_libsndfile_are_installed_without_recommends() -> None:
    run = " ".join(_named("RUN"))
    assert "apt-get install -y --no-install-recommends ffmpeg libsndfile1" in run
    assert "rm -rf /var/lib/apt/lists/*" in run


def test_there_is_a_loopback_healthcheck() -> None:
    checks = _named("HEALTHCHECK")
    assert len(checks) == 1
    assert checks[0].endswith('CMD ["python", "-m", "app.healthcheck", "api"]')


def test_the_build_context_is_an_allowlist() -> None:
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines[0] == "*"
    assert [x for x in lines if x.startswith("!")] == ["!requirements.txt", "!app/"]
    # ...and inside app/ nothing local or generated: every one excluded after the `!app/` that
    # would let it in again (a later line wins).
    after = lines[lines.index("!app/") + 1 :]
    required = {"**/__pycache__", "**/*.pyc", "**/*.pyo", "app/**/.env*", "app/**/*.db"}
    required |= {f"app/**/*.{ext}" for ext in AUDIO}
    assert required <= set(after), sorted(required - set(after))


AUDIO = ("wav", "mp3", "mp4", "m4a", "flac", "ogg", "webm")
WORKER_CHECK = ["CMD", "python", "-m", "app.healthcheck", "worker"]
DEV = ROOT / "docker-compose.yml"


def test_the_worker_overrides_the_api_healthcheck_in_both_compose_files() -> None:
    """The image's HEALTHCHECK asks the api on the loopback, which a worker never serves: each
    worker checks Redis instead, and only the worker does."""
    for path in (DEPLOY, DEV):
        services = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
        assert services["worker"]["healthcheck"]["test"] == WORKER_CHECK, path.name
        assert "healthcheck" not in services["api"], path.name


def test_redis_runs_unprivileged_with_no_capability() -> None:
    """The official image's entrypoint chowns /data and switches user only when started as root;
    started as its own redis user (999:1000) it needs no capability (checked by starting the
    deploy file locally, spotdown PR for the #70 review)."""
    redis = _deploy()["services"]["redis"]
    assert redis["user"] == "999:1000"
    assert redis["cap_drop"] == ["ALL"] and "cap_add" not in redis
    assert "no-new-privileges:true" in redis["security_opt"]


def test_the_deploy_file_pins_the_image_and_publishes_no_port() -> None:
    doc = _deploy()
    services = doc["services"]
    assert set(services) == {"redis", "api", "worker"}
    for name in ("api", "worker"):
        image = services[name]["image"]
        assert image.startswith("ghcr.io/magpern/spotdown:${SPOTDOWN_VERSION:?"), image
        assert "build" not in services[name]
    assert re.fullmatch(
        r"redis:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}", services["redis"]["image"]
    )
    for name, svc in services.items():
        assert "ports" not in svc and "network_mode" not in svc, name
    code = [
        line
        for line in DEPLOY.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not any("latest" in line for line in code)


def test_the_deploy_file_runs_hardened_and_read_only() -> None:
    services = _deploy()["services"]
    for name in ("api", "worker"):
        svc = services[name]
        assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert svc["volumes"] == ["spotdown-data:/data"], name
        assert svc["environment"]["SERVICE_MODE"] == "true", name
        assert "user" not in svc, name  # the image's 10001, never root
    assert services["redis"]["read_only"] is True


def test_only_the_api_gets_the_service_key_and_only_as_a_secret_file() -> None:
    doc = _deploy()
    services = doc["services"]
    assert services["api"]["secrets"] == ["spotdown_service_key"]
    assert "secrets" not in services["worker"] and "secrets" not in services["redis"]
    assert services["api"]["environment"]["SERVICE_KEY_FILE"] == "/run/secrets/spotdown_service_key"
    assert set(doc["secrets"]) == {"spotdown_service_key"}
    assert set(doc["secrets"]["spotdown_service_key"]) == {"file"}
    for svc in services.values():
        for name in svc.get("environment", {}):
            assert not re.search(r"KEY$|SECRET|TOKEN|PASSWORD", name), name


def test_redis_is_reachable_only_from_the_api_and_the_worker() -> None:
    doc = _deploy()
    services = doc["services"]
    networks = {name: set(svc["networks"]) for name, svc in services.items()}
    assert networks == {
        "redis": {"backend"},
        "api": {"backend", "service", "egress"},
        "worker": {"backend", "egress"},
    }
    assert doc["networks"]["backend"] == {"internal": True}
    assert doc["networks"]["service"]["external"] is True
    assert services["api"]["networks"]["service"] == {"aliases": ["spotdown-api"]}


UI_OFF = {"off", "false", "0", "no"}


def test_the_deploy_file_never_turns_the_ui_on() -> None:
    """ADR 0004: SPOTDOWN_UI is off unless an operator sets it. The deploy file may only pin it off,
    and never interpolates it (an empty ``${SPOTDOWN_UI:-}`` stops the start-up)."""
    for name, svc in _deploy()["services"].items():
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(item.split("=", 1) if "=" in item else (item, None) for item in env)
        if "SPOTDOWN_UI" in env:
            assert str(env["SPOTDOWN_UI"]).strip().lower() in UI_OFF, (name, env["SPOTDOWN_UI"])
    text = DEPLOY.read_text(encoding="utf-8")
    assert "${SPOTDOWN_UI" not in text and "$SPOTDOWN_UI" not in text


def test_the_image_and_the_env_example_leave_the_ui_off() -> None:
    assert _env().get("SPOTDOWN_UI", "off").lower() in UI_OFF
    example = (ROOT / "deploy" / "spotdown.env.example").read_text(encoding="utf-8")
    live = [line for line in example.splitlines() if line.strip() and not line.startswith("#")]
    assert not any(line.startswith("SPOTDOWN_UI") for line in live)
    assert "#SPOTDOWN_UI=off" in example
