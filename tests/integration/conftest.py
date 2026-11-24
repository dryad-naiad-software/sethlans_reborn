# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration-stage fixtures.

Resets worker job-claim state added by the Unreal foundation (chunk 3);
see ``tests/_worker_claim_state.py``. Points the settings file and the
settings folder at per-test paths for code that runs inside a test.

What this does not cover: pytest-django imports
``sethlans_manager.settings`` once at session start, before any fixture
runs. That import reads ``<repo>/manager/sethlans.ini`` (when present)
and, with no ``SETHLANS_SECURITY_SECRET_KEY``, creates the secret key in
the real per-user Sethlans folder (``<settings folder>/manager``).
Module-level values computed then (``config_loader._config``,
``settings.DATABASES``, ``logging_config.LOGS_DIR``) keep that start's
paths for the whole session.
"""
import pytest

from tests._worker_claim_state import reset_worker_claim_state

SETTINGS_FILE_ENV = "SETHLANS_SETTINGS_FILE"
SETTINGS_DIR_ENV = "SETHLANS_SETTINGS_DIR"


@pytest.fixture(autouse=True)
def _isolated_settings_file(monkeypatch, tmp_path):
    """Point the settings file and folder at a per-test folder that is empty.

    Applies to code called during the test (and to subprocesses it
    starts), not to the session-start settings import described in the
    module docstring.

    ``SETHLANS_SETTINGS_FILE``: without it, source-mode code resolves
    ``<repo>/manager/sethlans.ini``. ``SETHLANS_SETTINGS_DIR``: without it,
    identity files (the secret key, the worker's ``config.json``) and the
    worker's default data folder resolve to the real per-user Sethlans
    folder.

    Subprocesses that copy ``os.environ`` inherit both. In
    ``settings_path()`` the file outranks ``SETHLANS_SETTINGS_DIR`` and
    ``SETHLANS_MANAGER_DATA_DIR``, and the folder outranks
    ``SETHLANS_MANAGER_DATA_DIR``: a helper that gives a child its own
    settings folder sets ``SETHLANS_SETTINGS_DIR`` and pops
    ``SETHLANS_SETTINGS_FILE`` from the child's env. Tests of the
    resolution order clear them.
    """
    folder = tmp_path / "_settings"
    monkeypatch.setenv(SETTINGS_FILE_ENV, str(folder / "sethlans.ini"))
    monkeypatch.setenv(SETTINGS_DIR_ENV, str(folder))


@pytest.fixture(autouse=True)
def _reset_worker_claim_state():
    reset_worker_claim_state()
    yield
    reset_worker_claim_state()
