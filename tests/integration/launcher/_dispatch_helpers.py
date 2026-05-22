# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the ``apply_pending_setup`` dispatch / pipeline test
splits (issue #191 / frozen_apply_pipeline spec).

Leading-underscore filename keeps pytest from collecting this module as
a test file. Lives next to the test modules so they can share the
identical ``pending_setup.json`` writer and the curated-env builder
without per-file duplication.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
MANAGER_DIR = REPO_ROOT / "manager"
RUN_MANAGER_PY = MANAGER_DIR / "run_manager.py"

STRONG_PASSWORD = "Tr0pical!Mongoose-Hops42"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_pending(
    data_dir: Path,
    *,
    schema_version: int = 1,
    topology: str = "manager",
    auto_enroll: bool = False,
    created_at: float | None = None,
    username: str = "admin_dispatch",
    email: str = "admin@example.com",
    password: str = STRONG_PASSWORD,
) -> Path:
    """Write a valid ``pending_setup.json`` fixture to *data_dir*."""
    if created_at is None:
        created_at = time.time()
    payload = {
        "schema_version": schema_version,
        "topology": topology,
        "created_at_unix": created_at,
        "admin_user": {
            "username": username,
            "email": email,
            "password_plaintext": password,
        },
        "worker_ui_password_hash": None,
        "worker_ui_password_salt": None,
        "auto_enroll_local_worker": auto_enroll,
    }
    target = data_dir / "pending_setup.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _curated_env_for_subprocess() -> dict:
    """Build a minimal env suitable for running ``run_manager.py`` subprocesses.

    Mirrors ``build_curated_env`` in ``launcher/apply_pending_setup.py``
    but adds ``SETHLANS_SECURITY_DEBUG=True`` (required by pytest.ini) and
    preserves the project-root PYTHONPATH so Django + ``sethlans_manager``
    are importable from the test venv.
    """
    env: dict = {}
    env["DJANGO_SETTINGS_MODULE"] = "sethlans_manager.settings"
    env["SETHLANS_SECURITY_DEBUG"] = "True"
    # Inherit PYTHONPATH from the test process (pytest has already appended
    # manager/ and the repo root via pytest.ini ``pythonpath``).
    extra = os.pathsep.join([
        str(REPO_ROOT),
        str(MANAGER_DIR),
        str(REPO_ROOT / "worker"),
    ])
    existing = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{extra}{os.pathsep}{existing}" if existing else extra)
    # Pass through PATH so the venv Python is findable.
    env["PATH"] = os.environ.get("PATH", "")
    if sys.platform == "win32":
        for var in ("SystemRoot", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                    "TEMP", "TMP", "COMSPEC", "PATHEXT"):
            val = os.environ.get(var)
            if val:
                env[var] = val
    else:
        env["HOME"] = os.environ.get("HOME", "/")
    return env
