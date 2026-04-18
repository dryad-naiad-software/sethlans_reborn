# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for SECRET_KEY bootstrap wiring in Django settings.

Uses subprocesses so each case cold-imports ``sethlans_manager.settings``
with a clean environment — this matches real first-boot behavior and
avoids polluting the in-process Django app registry.
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

out = {{
    'secret_key': settings.SECRET_KEY,
    'length': len(settings.SECRET_KEY),
}}
sys.stdout.write(json.dumps(out))
"""


def _run_settings_probe(env_overrides: dict) -> dict:
    """Cold-import settings in a subprocess, return a dict of probed values."""
    env = os.environ.copy()
    # Scrub overrides that would otherwise leak from the parent process.
    for leaky in (
        "SETHLANS_SECURITY_SECRET_KEY",
        "SETHLANS_MANAGER_DATA_DIR",
        "DJANGO_SETTINGS_MODULE",
    ):
        env.pop(leaky, None)
    env.update(env_overrides)
    # Ensure the child isn't in DEBUG mode — we want to prove the old
    # ImproperlyConfigured crash is gone on a truly fresh install.
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


def test_first_boot_generates_and_persists_key(tmp_path):
    """Fresh data dir + no env override: settings self-bootstrap a key."""
    data_dir = tmp_path / "manager-data"
    # Do NOT pre-create — loader must handle missing dir.

    probed = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    })

    assert probed["length"] >= 50
    # The insecure dev default should no longer be the effective key.
    assert "django-insecure-^&r@p" not in probed["secret_key"]

    key_file = data_dir / "secret_key"
    assert key_file.exists()
    assert key_file.read_text(encoding="utf-8").strip() == probed["secret_key"]


def test_env_var_precedence_skips_file(tmp_path):
    """Env-var override wins; no key file is created."""
    data_dir = tmp_path / "manager-data"
    custom = (
        "custom-test-key-with-enough-entropy-to-pass-django-checks-0123456789"
    )

    probed = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
        "SETHLANS_SECURITY_SECRET_KEY": custom,
    })

    assert probed["secret_key"] == custom
    assert not (data_dir / "secret_key").exists()


def test_persisted_key_survives_reboot(tmp_path):
    """Two cold-boots return the same persisted key."""
    data_dir = tmp_path / "manager-data"

    first = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    })
    second = _run_settings_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    })

    assert first["secret_key"] == second["secret_key"]
