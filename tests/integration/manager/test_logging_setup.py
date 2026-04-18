# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test for manual logging bootstrap (issue #64).

Cold-imports ``sethlans_manager.settings`` in a subprocess, calls
``django.setup()`` + ``logging_config.configure()``, and asserts that the
``django`` logger ends up with our handlers and NO ``AdminEmailHandler``.

Uses subprocess isolation (same pattern as ``test_secret_key_settings``)
because Django's logging state is process-global and a reload inside the
pytest-django host process would interfere with sibling tests.
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
import logging
import os
import sys

sys.path.insert(0, {manager_dir!r})
sys.path.insert(0, {repo_root!r})

os.environ['DJANGO_SETTINGS_MODULE'] = 'sethlans_manager.settings'

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
from sethlans_manager.logging_config import configure  # noqa: E402

configure()

django_logger = logging.getLogger('django')
handler_classes = [type(h).__name__ for h in django_logger.handlers]

out = {{
    'logging_config': settings.LOGGING_CONFIG,
    'handler_classes': handler_classes,
    'has_mail_admins_handler': any(
        'AdminEmailHandler' in c for c in handler_classes
    ),
    'handler_count': len(django_logger.handlers),
    'mail_admins_in_dict': 'mail_admins' in settings.LOGGING['handlers'],
}}
sys.stdout.write(json.dumps(out))
"""


def _run_probe(env_overrides: dict) -> dict:
    env = os.environ.copy()
    for leaky in (
        "SETHLANS_SECURITY_SECRET_KEY",
        "SETHLANS_MANAGER_DATA_DIR",
        "DJANGO_SETTINGS_MODULE",
    ):
        env.pop(leaky, None)
    env.update(env_overrides)
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
            "Logging probe failed.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return json.loads(result.stdout)


def test_configure_installs_handlers_without_mail_admins(tmp_path):
    """Cold-boot path: explicit configure() installs our handlers.

    Asserts:
      - ``settings.LOGGING_CONFIG`` is ``None`` (Django bootstrap disabled).
      - The resulting ``django`` logger has at least one handler.
      - NO ``AdminEmailHandler`` is attached — the import chain that
        triggered issue #64 is gone.
      - ``mail_admins`` is not in our user-level ``LOGGING`` dict.
    """
    data_dir = tmp_path / "manager-data"

    probed = _run_probe({
        "SETHLANS_MANAGER_DATA_DIR": str(data_dir),
    })

    assert probed["logging_config"] is None
    assert probed["handler_count"] >= 1
    assert probed["has_mail_admins_handler"] is False
    assert probed["mail_admins_in_dict"] is False
