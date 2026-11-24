# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the settings file path that ``sethlans_manager.settings``
reads (``config_loader._config_file_path``, from
:func:`shared.settings_file.settings_path`).

Uses a subprocess so the module-level env-var read happens cold, the
same way the real ``manage.py`` / ``run_manager.py`` entry points see
it. See ``tests/integration/manager/test_secret_key_settings.py`` for
the established pattern.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"


DRIVER_SCRIPT = """
import json
import os
import sys

# Prepend manager/ so 'sethlans_manager' imports work like real manage.py.
sys.path.insert(0, {manager_dir!r})
sys.path.insert(0, {repo_root!r})

os.environ['DJANGO_SETTINGS_MODULE'] = 'sethlans_manager.settings'

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
from sethlans_manager import config_loader  # noqa: E402

out = {{
    'config_file_path': str(config_loader._config_file_path),
    'base_dir': str(settings.BASE_DIR),
    'secret_key_length': len(settings.SECRET_KEY),
}}
sys.stdout.write(json.dumps(out))
"""


def _run_settings_probe(env_overrides: dict, home: Path) -> dict:
    """Cold-import settings in a subprocess, return probed values.

    *home* stands in for the per-user folders (``LOCALAPPDATA``,
    ``XDG_DATA_HOME``, ``HOME``), so the generated secret key never lands
    in the real per-user Sethlans folder.
    """
    env = os.environ.copy()
    # Scrub overrides that could leak from the parent process (pytest.ini
    # sets SETHLANS_SECURITY_DEBUG; the unit conftest sets the settings
    # file; dev machines may export data folders).
    for leaky in (
        "SETHLANS_SECURITY_SECRET_KEY",
        "SETHLANS_MANAGER_DATA_DIR",
        "SETHLANS_SETTINGS_FILE",
        "SETHLANS_SETTINGS_DIR",
        "SETHLANS_DATA_ROOT",
        "SETHLANS_SECURITY_DEBUG",
        "DJANGO_SETTINGS_MODULE",
    ):
        env.pop(leaky, None)
    for name in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME", "USERPROFILE"):
        env[name] = str(home)
    env.update(env_overrides)
    # Keep the child out of DEBUG so it doesn't trip fresh-install guards.
    env.setdefault("SETHLANS_SECURITY_DEBUG", "False")

    script = DRIVER_SCRIPT.format(
        manager_dir=str(MANAGER_DIR),
        repo_root=str(REPO_ROOT),
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Settings probe failed.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return json.loads(result.stdout)


def test_ini_path_honors_data_dir_env_var(tmp_path):
    """With SETHLANS_MANAGER_DATA_DIR set, the settings file lives under it."""
    data_dir = tmp_path / "manager-data"

    probed = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    }, tmp_path / "home")

    resolved = Path(probed["config_file_path"])
    assert resolved == data_dir / "sethlans.ini"
    # Sanity: not the in-repo dev file.
    assert resolved != Path(probed["base_dir"]) / "sethlans.ini"


def test_ini_path_falls_back_to_base_dir_without_env_var(tmp_path):
    """Without any env var, the source-mode file is BASE_DIR/sethlans.ini."""
    probed = _run_settings_probe({}, tmp_path / "home")

    resolved = Path(probed["config_file_path"])
    assert resolved == Path(probed["base_dir"]) / "sethlans.ini"
    # The secret key went to the stand-in per-user folder's manager/.
    keys = list((tmp_path / "home").rglob("secret_key"))
    assert [key.parent.name for key in keys] == ["manager"]


def test_settings_file_env_wins(tmp_path):
    """``SETHLANS_SETTINGS_FILE`` outranks ``SETHLANS_MANAGER_DATA_DIR``."""
    explicit = tmp_path / "explicit" / "sethlans.ini"
    probed = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(tmp_path / "manager-data"),
        "SETHLANS_SETTINGS_FILE": str(explicit),
    }, tmp_path / "home")

    assert Path(probed["config_file_path"]) == explicit
