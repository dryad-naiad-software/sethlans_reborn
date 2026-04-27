# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared fixtures + path constants for the run_wizard.py entry-point
tests.

Phase F4 split ``test_run_wizard_entry.py`` into a lifecycle / CLI
half and a successful-wiring half; both halves need the same
``provisioned_data_dir`` fixture and the wizard-on-path autouse
fixture.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WIZARD_DIR = PROJECT_ROOT / "wizard"
RUN_WIZARD = WIZARD_DIR / "run_wizard.py"


@pytest.fixture(autouse=True)
def _ensure_wizard_on_path():
    """Make the ``wizard/`` script directory importable.

    Mirrors ``test_package_scaffold._ensure_wizard_on_path``.
    """
    wizard_dir = str(WIZARD_DIR)
    added = False
    if wizard_dir not in sys.path:
        sys.path.insert(0, wizard_dir)
        added = True
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(wizard_dir)
            except ValueError:
                pass


@pytest.fixture
def provisioned_data_dir(tmp_path, monkeypatch):
    """A tmp data dir with chmod-600 ``.setup_token`` + ``.ipc_secret``.

    Mirrors what the launcher's FR-L3a / FR-L4a writes immediately
    before spawning the wizard.
    """
    monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
    wizard_subdir = tmp_path / "wizard"
    wizard_subdir.mkdir(parents=True, exist_ok=True)

    token = b"setup-token-deadbeef-cafe-ffff"
    secret = b"ipc-hmac-secret-bytes-aaaa-bbbb"
    (wizard_subdir / ".setup_token").write_bytes(token)
    (wizard_subdir / ".ipc_secret").write_bytes(secret)
    if os.name != "nt":
        os.chmod(wizard_subdir / ".setup_token", 0o600)
        os.chmod(wizard_subdir / ".ipc_secret", 0o600)
    return tmp_path, token, secret
