# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager.ini`` path resolution in
``sethlans_manager.settings``.

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
from sethlans_manager import settings as settings_mod  # noqa: E402

out = {{
    'config_file_path': str(settings_mod._config_file_path),
    'base_dir': str(settings_mod.BASE_DIR),
    'secret_key_length': len(settings.SECRET_KEY),
}}
sys.stdout.write(json.dumps(out))
"""


def _run_settings_probe(env_overrides: dict) -> dict:
    """Cold-import settings in a subprocess, return probed values."""
    env = os.environ.copy()
    # Scrub overrides that could leak from the parent process (pytest.ini
    # sets SETHLANS_SECURITY_DEBUG; dev machines may export DATA_DIR).
    for leaky in (
        "SETHLANS_SECURITY_SECRET_KEY",
        "SETHLANS_MANAGER_DATA_DIR",
        "SETHLANS_SECURITY_DEBUG",
        "DJANGO_SETTINGS_MODULE",
    ):
        env.pop(leaky, None)
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
    """With SETHLANS_MANAGER_DATA_DIR set, ini path lives under tmpdir."""
    data_dir = tmp_path / "manager-data"

    probed = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    })

    resolved = Path(probed["config_file_path"])
    expected = data_dir / "manager.ini"
    assert resolved == expected, (
        f"Expected ini path {expected}, got {resolved}"
    )
    # Sanity: not the in-repo dev file.
    assert resolved != Path(probed["base_dir"]) / "manager.ini"


def test_ini_path_falls_back_to_base_dir_without_env_var(tmp_path):
    """Without the env var, ini path preserves BASE_DIR/manager.ini fallback."""
    # tmp_path unused but kept for symmetry + pytest isolation.
    probed = _run_settings_probe({})

    resolved = Path(probed["config_file_path"])
    expected = Path(probed["base_dir"]) / "manager.ini"
    assert resolved == expected, (
        f"Expected dev fallback {expected}, got {resolved}"
    )
