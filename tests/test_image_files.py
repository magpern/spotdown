"""The backend image and the deploy files (spotdown#66 and #79, docs/runbooks/docker-image.md): the
Dockerfile, ``.dockerignore``, ``deploy/compose.yaml`` with the UI image's ``ui`` service, and its
overlays ``compose.no-ui.yaml`` and ``compose.spotify.yaml``. Read from the files; nothing is built
or started. The UI image's own Dockerfile is checked by ``tests/ui_app/test_ui_image.py``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DEPLOY = ROOT / "deploy" / "compose.yaml"
NO_UI = ROOT / "deploy" / "compose.no-ui.yaml"
SPOTIFY = ROOT / "deploy" / "compose.spotify.yaml"
SECRET_NAME = re.compile(r"KEY$|SECRET$|TOKEN$|PASSWORD")
PUBLISH = "${SPOTDOWN_PUBLISH_HOST:-127.0.0.1}:8000:8000"


class _TaggedLoader(yaml.SafeLoader):
    """Reads Compose's ``!override`` and ``!reset`` tags as ``(tag, value)`` pairs."""


def _tagged(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    tag = "!" + suffix
    if isinstance(node, yaml.SequenceNode):
        return (tag, loader.construct_sequence(node))
    if isinstance(node, yaml.MappingNode):
        return (tag, loader.construct_mapping(node))
    return (tag, loader.construct_scalar(node))  # type: ignore[arg-type]


_TaggedLoader.add_multi_constructor("!", _tagged)


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


def _overlay(path: Path) -> Dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TaggedLoader)  # noqa: S506


def _environment(svc: Dict[str, Any]) -> Dict[str, Any]:
    env = svc.get("environment") or {}
    assert isinstance(env, dict), env  # a mapping, so every name and value can be checked
    return env


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


def test_the_image_sets_no_mode_and_data_lives_in_one_place() -> None:
    env = _env()
    for name in ("SERVICE_MODE", "SERVE_UI", "SPOTDOWN_UI"):
        assert name not in env, name
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


def test_the_deploy_file_pins_both_images_and_publishes_only_the_ui() -> None:
    doc = _deploy()
    services = doc["services"]
    assert set(services) == {"redis", "api", "worker", "ui"}
    images = {
        "api": "ghcr.io/magpern/spotdown:${SPOTDOWN_VERSION:?",
        "worker": "ghcr.io/magpern/spotdown:${SPOTDOWN_VERSION:?",
        "ui": "ghcr.io/magpern/spotdown-ui:${SPOTDOWN_VERSION:?",
    }
    for name, prefix in images.items():
        assert services[name]["image"].startswith(prefix), name
        assert "build" not in services[name], name
    assert re.fullmatch(
        r"redis:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}", services["redis"]["image"]
    )
    for name, svc in services.items():
        assert "network_mode" not in svc and "expose" not in svc, name
        if name != "ui":
            assert "ports" not in svc, name
    assert services["ui"]["ports"] == [PUBLISH]
    code = [
        line
        for line in DEPLOY.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not any("latest" in line for line in code)


def test_the_deploy_file_runs_hardened_and_read_only() -> None:
    services = _deploy()["services"]
    volumes = {"api": "spotdown-data:/data", "worker": "spotdown-data:/data"}
    volumes["ui"] = "spotdown-ui-data:/data"
    for name, volume in volumes.items():
        svc = services[name]
        assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert svc["volumes"] == [volume], name
        assert "user" not in svc and "privileged" not in svc, name  # the images' 10001
        assert any(t.startswith("/tmp:") for t in svc["tmpfs"]), name
        for mode in ("SERVICE_MODE", "SERVE_UI", "SPOTDOWN_UI"):
            assert mode not in _environment(svc), (name, mode)
    assert services["redis"]["read_only"] is True


# Each secret, the services it is mounted into, and the env name that points at it (ADR 0005).
SECRETS = {
    "spotdown_service_key": {"api": "SERVICE_KEY_FILE"},
    "spotdown_ui_backend_key": {"api": "UI_SERVICE_KEY_FILE", "ui": "SPOTDOWN_BACKEND_KEY_FILE"},
}


def _mounts(services: Dict[str, Any]) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for name, svc in services.items():
        for secret in svc.get("secrets", []):
            out.setdefault(secret, set()).add(name)
    return out


def test_each_secret_is_mounted_only_into_its_services_as_a_file() -> None:
    doc = _deploy()
    services = doc["services"]
    assert _mounts(services) == {s: set(where) for s, where in SECRETS.items()}
    for secret, where in SECRETS.items():
        assert set(doc["secrets"][secret]) == {"file"}, secret
        for svc, env_name in where.items():
            assert _environment(services[svc])[env_name] == f"/run/secrets/{secret}", svc
    assert set(doc["secrets"]) == set(SECRETS)


def test_no_secret_is_an_environment_value() -> None:
    """Every name that looks like a secret ends in ``_FILE`` and points into /run/secrets, in the
    deploy file and its overlays; no env file is a secret's home."""
    services = dict(_deploy()["services"])
    for path in (NO_UI, SPOTIFY):
        for name, svc in _overlay(path)["services"].items():
            services[f"{path.name}:{name}"] = svc
    for name, svc in services.items():
        for key, value in _environment(svc).items():
            assert not SECRET_NAME.search(key), (name, key)
            if key.endswith("KEY_FILE") or "SECRET" in key:
                assert (
                    value is None or isinstance(value, tuple) or value.startswith("/run/secrets/")
                ), (name, key)


def test_a_deploy_without_the_ui_needs_only_classbeats_key() -> None:
    """``compose.no-ui.yaml``: the ui is never started, and the api's secrets are replaced by
    classbeat's key alone, with no UI key path left in its environment."""
    services = _overlay(NO_UI)["services"]
    assert set(services) == {"api", "ui"}
    assert services["ui"] == {"profiles": ["ui-off"]}
    assert services["api"]["secrets"] == ("!override", ["spotdown_service_key"])
    assert services["api"]["environment"] == {"UI_SERVICE_KEY_FILE": ("!reset", "null")}
    assert "secrets" not in _overlay(NO_UI)


def test_the_spotify_secret_is_an_optional_file_mounted_into_the_api_only() -> None:
    doc = _overlay(SPOTIFY)
    assert set(doc["services"]) == {"api"}
    api = doc["services"]["api"]
    assert api["secrets"] == ["spotdown_spotify_client_secret"]
    want = "/run/secrets/spotdown_spotify_client_secret"
    assert api["environment"] == {"SPOTIFY_CLIENT_SECRET_FILE": want}
    assert set(doc["secrets"]) == {"spotdown_spotify_client_secret"}
    assert set(doc["secrets"]["spotdown_spotify_client_secret"]) == {"file"}
    # The base deploy file starts without it: no service there names or mounts it.
    base = _deploy()
    assert not any("spotify" in s for s in base["secrets"])
    for name, svc in base["services"].items():
        assert not any("SPOTIFY" in k for k in _environment(svc)), name


def test_the_networks_keep_redis_private_and_the_ui_on_the_service_network() -> None:
    doc = _deploy()
    services = doc["services"]
    networks = {name: set(svc["networks"]) for name, svc in services.items()}
    assert networks == {
        "redis": {"backend"},
        "api": {"backend", "service", "egress"},
        "worker": {"backend", "egress"},
        "ui": {"service", "ui-published"},
    }
    assert doc["networks"]["backend"] == {"internal": True}
    assert doc["networks"]["service"]["external"] is True
    assert services["api"]["networks"]["service"] == {"aliases": ["spotdown-api"]}
    assert _environment(services["ui"])["SPOTDOWN_BACKEND_URL"] == "http://spotdown-api:8000"


def test_the_worker_keeps_classbeats_queue_and_needs_no_key() -> None:
    from app.queue import QUEUE_NAME

    worker = _deploy()["services"]["worker"]
    assert worker["command"][-1] == QUEUE_NAME == "spotdown"
    assert "secrets" not in worker and "SERVICE_KEY_FILE" not in _environment(worker)
    dev = yaml.safe_load(DEV.read_text(encoding="utf-8"))["services"]["worker"]
    assert dev["command"].split()[-1] == QUEUE_NAME


def test_the_healthcheck_module_and_its_two_checks_survive() -> None:
    from app import healthcheck

    assert set(healthcheck.CHECKS) == {"api", "worker"}
    assert (ROOT / "app" / "healthcheck.py").is_file()


def test_the_image_holds_no_ui_files() -> None:
    assert not (ROOT / "app" / "static").exists()
    for ext in ("html", "js", "css"):
        assert not list((ROOT / "app").rglob(f"*.{ext}")), ext


def test_the_dev_compose_file_runs_both_images_with_the_ui_on_loopback() -> None:
    doc = yaml.safe_load(DEV.read_text(encoding="utf-8"))
    services = doc["services"]
    assert set(services) == {"redis", "api", "worker", "ui"}
    assert services["ui"]["build"] == {"context": ".", "dockerfile": "Dockerfile.ui"}
    assert services["ui"]["ports"] == [PUBLISH]
    for name in ("redis", "api", "worker"):
        assert "ports" not in services[name], name
    assert _mounts(services) == {s: set(where) for s, where in SECRETS.items()}
