"""The UI package and image files (spotdown#78 acceptance 6): ``spotdown_ui`` imports nothing from
``app``, and ``Dockerfile.ui`` and its build context hold no ``app/``, no yt-dlp, librosa or ffmpeg
and no secret. Read from the files; nothing is built."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List

from .helpers import ROOT

PACKAGE = ROOT / "spotdown_ui"
DOCKERFILE = ROOT / "Dockerfile.ui"
IGNORE = ROOT / "Dockerfile.ui.dockerignore"
BACKEND_ONLY = ("yt-dlp", "librosa", "redis", "rq", "mutagen", "python-multipart")


def _imports(path: Path) -> List[str]:
    """Every module a file imports, relative imports resolved inside ``spotdown_ui``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            if node.level > len(parts):
                out.append("<beyond the package>")
                continue
            base = parts[: len(parts) - node.level + 1] if node.level else []
            module = ".".join(base + ([node.module] if node.module else []))
            if node.module:
                out.append(module)
            else:  # `from .. import x` imports the module x of that package
                out += [".".join(filter(None, [module, a.name])) for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
            out.append("<dynamic>")
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
            out.append("<dynamic>")
    return out


def test_spotdown_ui_imports_nothing_from_app() -> None:
    files = sorted(PACKAGE.rglob("*.py"))
    assert len(files) >= 10
    for path in files:
        for name in _imports(path):
            assert name != "app" and not name.startswith("app."), (path.name, name)
            assert name not in ("<dynamic>", "<beyond the package>"), path.name
            assert name.split(".")[0] != "tests", (path.name, name)


def test_the_import_check_sees_an_app_import(tmp_path) -> None:
    """The checker itself: absolute, from-, and relative-beyond-the-package imports are found."""
    probe = PACKAGE / "_probe_tmp.py"
    try:
        probe.write_text("import app.main\nfrom app import db\nfrom .. import app as x\n"
                         "from ... import y\n")  # fmt: skip
        names = _imports(probe)
    finally:
        probe.unlink()
    assert names == ["app.main", "app", "<beyond the package>", "<beyond the package>"]


def test_importing_spotdown_ui_loads_no_app_module() -> None:
    import subprocess
    import sys

    script = (
        "import sys, spotdown_ui.app, spotdown_ui.healthcheck\n"
        "print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')))\n"
    )
    run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                         timeout=120)  # fmt: skip
    assert run.returncode == 0, run.stderr[-2000:]
    assert run.stdout.strip() == "[]"


def _instructions() -> List[str]:
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


def test_the_ui_image_copies_only_its_requirements_and_package() -> None:
    assert _named("COPY") == ["requirements-ui.txt .", "spotdown_ui ./spotdown_ui"]
    assert not _named("ADD") and not _named("ARG")
    text = " ".join(_instructions())
    assert "app/" not in text.replace("spotdown_ui", "") and "./app" not in text
    assert not re.search(r"ffmpeg|libsndfile|yt-dlp|librosa|apt-get", text)


def test_the_ui_image_shares_the_backend_base() -> None:
    backend = [
        line for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("FROM ")
    ]
    assert _named("FROM") == [backend[0][5:]]
    assert re.fullmatch(r"python:3\.11\.\d+-slim-[a-z]+@sha256:[0-9a-f]{64}", _named("FROM")[0])


def test_the_ui_image_runs_unprivileged_with_a_healthcheck_on_8000() -> None:
    instructions = _instructions()
    assert _named("USER") == ["10001:10001"]
    last_run = max(i for i, x in enumerate(instructions) if x.startswith("RUN "))
    assert instructions.index("USER 10001:10001") > last_run
    assert _named("EXPOSE") == ["8000"]
    checks = _named("HEALTHCHECK")
    assert len(checks) == 1 and checks[0].endswith(
        'CMD ["python", "-m", "spotdown_ui.healthcheck"]'
    )
    cmd = _named("CMD")[-1]
    assert '"--factory", "spotdown_ui.app:create_app"' in cmd and '"--port", "8000"' in cmd
    assert '"--no-proxy-headers"' in cmd and "--workers" not in cmd  # one process: one slot


def test_the_ui_image_holds_no_secret_value() -> None:
    env: Dict[str, str] = {}
    for line in _named("ENV"):
        env.update(re.findall(r"([A-Z_]+)=(\S+)", line))
    assert env["SPOTDOWN_BACKEND_KEY_FILE"].startswith("/run/secrets/")
    assert env["UI_DATA_DIR"] == "/data"
    for name in env:
        assert not re.search(r"KEY$|SECRET|TOKEN$|PASSWORD", name), name


def test_the_ui_build_context_is_an_allowlist_without_app() -> None:
    lines = [line.strip() for line in IGNORE.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]  # fmt: skip
    assert lines[0] == "*"
    assert [x for x in lines if x.startswith("!")] == ["!requirements-ui.txt", "!spotdown_ui/"]
    after = lines[lines.index("!spotdown_ui/") + 1 :]
    for pattern in ("**/__pycache__", "**/*.pyc", "spotdown_ui/**/.env*", "spotdown_ui/**/*.mp3"):
        assert pattern in after, pattern


def _pins(path: Path) -> Dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-r"):
            name, _, version = line.partition("==")
            out[re.sub(r"\[.*\]", "", name).lower()] = version
    return out


def test_requirements_ui_is_small_and_pinned_like_the_backend() -> None:
    ui, backend = _pins(ROOT / "requirements-ui.txt"), _pins(ROOT / "requirements.txt")
    assert set(ui) == {"fastapi", "uvicorn", "pydantic", "httpx"}
    for name, version in ui.items():
        assert version and backend[name] == version, name
    assert not set(BACKEND_ONLY) & set(ui)
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert "-r requirements-ui.txt" in dev  # CI installs it and runs these tests
