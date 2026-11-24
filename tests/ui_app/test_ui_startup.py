"""Start-up fails closed (spotdown#78 acceptance 5): without the key file, with a short key, a bad
``SPOTDOWN_BACKEND_URL`` or a bad host entry the app is not built. No error carries the key."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from spotdown_ui.app import create_app
from spotdown_ui.backend_key import BackendKey, BackendKeyError
from spotdown_ui.exposure_policy import SettingsError
from spotdown_ui.settings import parse_backend_url, parse_settings

from .helpers import KEY, ROOT, ui_env


def test_a_good_environment_builds(tmp_path) -> None:
    app = create_app(ui_env(tmp_path))
    assert app.state.backend is not None


def test_no_backend_url_or_key_file_setting_fails(tmp_path) -> None:
    env = ui_env(tmp_path)
    for name in ("SPOTDOWN_BACKEND_URL", "SPOTDOWN_BACKEND_KEY_FILE"):
        broken = {k: v for k, v in env.items() if k != name}
        with pytest.raises(SettingsError, match=name):
            create_app(broken)


@pytest.mark.parametrize("case", ["missing", "directory", "empty", "short", "space", "binary"])
def test_a_bad_key_file_fails_without_its_content(tmp_path, case) -> None:
    env = ui_env(tmp_path)
    path = tmp_path / "backend_key"
    secret = "s3cr3t-" + "k" * 24  # 31 characters
    if case == "missing":
        path.unlink()
    elif case == "directory":
        path.unlink()
        path.mkdir()
    elif case == "empty":
        path.write_text("")
    elif case == "short":
        path.write_text(secret + "\n")
    elif case == "space":
        path.write_text("a key with spaces in it, long enough to pass")
    else:
        path.write_bytes(b"\xff\xfe" + b"k" * 40)
    with pytest.raises(BackendKeyError) as err:
        create_app(env)
    assert secret not in str(err.value) and "spaces in it" not in str(err.value)
    assert not (tmp_path / "data" / "ui_token").exists()  # nothing was started


def test_the_key_never_shows_in_a_repr() -> None:
    key = BackendKey(KEY)
    assert KEY not in repr(key) and KEY not in str(key) and KEY not in f"{key}"
    assert key.authorization() == "Bearer " + KEY
    with pytest.raises(AttributeError):
        key.__dict__  # noqa: B018 - slots only: no instance dict to dump


@pytest.mark.parametrize(
    "url",
    ["", "spotdown-api:8000", "ftp://spotdown-api", "http://", "http://user@spotdown-api:8000",
     "http://u:p@spotdown-api", "http://spotdown-api:8000/v2", "http://spotdown-api/x",
     "http://spotdown-api?x=1", "http://spotdown-api#f", "http://spotdown-api:0",
     "http://spotdown-api:99999", "http://spot down", "http://-bad-.example", "http://a..b",
     "https://spotdown-api:8000//", " http://spotdown-api", "http://spotdown-api:8000 ",
     "http://[::1", "http://1.2.3", "file:///etc/passwd", "http://spotdown-api\\@evil"],
)  # fmt: skip
def test_a_bad_backend_url_fails(tmp_path, url) -> None:
    env = ui_env(tmp_path, SPOTDOWN_BACKEND_URL=url)
    with pytest.raises(SettingsError):
        create_app(env)


@pytest.mark.parametrize(
    "url,base",
    [("http://spotdown-api:8000", "http://spotdown-api:8000"),
     ("http://spotdown-api:8000/", "http://spotdown-api:8000"),
     ("https://API.Example", "https://api.example:443"),
     ("http://10.0.0.5", "http://10.0.0.5:80"),
     ("http://[0:0::1]:9000", "http://[::1]:9000")],
)  # fmt: skip
def test_a_good_backend_url_is_normalised(url, base) -> None:
    assert parse_backend_url(url).base_url == base


@pytest.mark.parametrize(
    "name,value",
    [("UI_ALLOWED_HOSTS", "localhost,"), ("UI_ALLOWED_HOSTS", "*"),
     ("UI_ALLOWED_HOSTS", "localhost:8000"), ("UI_ALLOWED_HOSTS", "http://ui.example"),
     ("UI_ALLOWED_HOSTS", "ui.example/x"), ("UI_ALLOWED_HOSTS", " ui.example"),
     ("UI_PUBLIC_ORIGIN", "https://ui.example/"), ("UI_PUBLIC_ORIGIN", "https://ui.example:443"),
     ("UI_PUBLIC_ORIGIN", "ftp://ui.example"), ("MAX_UPLOAD_BYTES", "0"),
     ("MAX_UPLOAD_BYTES", "lots"), ("UI_UPLOAD_READ_S", "-1"), ("UI_UPLOAD_READ_S", "nan"),
     ("UI_BACKEND_TIMEOUT_S", "0")],
)  # fmt: skip
def test_a_bad_ui_setting_fails(tmp_path, name, value) -> None:
    with pytest.raises(SettingsError):
        parse_settings(ui_env(tmp_path, **{name: value}))


def test_the_factory_fails_closed_in_a_real_process(tmp_path) -> None:
    """``uvicorn --factory spotdown_ui.app:create_app`` builds from the environment: a short key
    stops it before it listens, and the error names the setting, never the key."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SPOTDOWN_", "UI_"))}
    env.update(ui_env(tmp_path, key="short-key-value"))
    script = "from spotdown_ui.app import create_app\ncreate_app()\n"
    run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=120)  # fmt: skip
    assert run.returncode != 0
    assert "SPOTDOWN_BACKEND_KEY_FILE" in run.stderr
    assert "short-key-value" not in run.stderr + run.stdout
