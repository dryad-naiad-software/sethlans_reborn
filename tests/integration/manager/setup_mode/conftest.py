# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fixtures for the setup-mode integration tests.

Every test gets ``LOCALAPPDATA`` and ``SETHLANS_SETTINGS_DIR`` under its
own ``tmp_path`` (the integration conftest already isolates the settings
file), and the ``SETHLANS_DB_*`` overrides of the run-time database are
removed, so the answers alone decide the database a probe opens. Child
processes started here are always ended at teardown.

``data_root`` is the data folder a submit names. Setup refuses system
folders, and on macOS ``tmp_path`` is under ``/private/var``, so there it
is a unique folder under ``~/.cache/sethlans-tests`` (``tests._home_scratch``),
removed at teardown; stale ones from killed runs are swept once per session.
"""
import os
import sys

import pytest

from tests._home_scratch import scratch_folder, sweep_stale

from .caddy_front import CaddyFront, caddy_binary_or_skip, make_cert
from .manager_process import ManagerProcess
from .setup_process import SetupProcess


@pytest.fixture(autouse=True)
def _isolated_user_folders(monkeypatch, tmp_path):
    local = tmp_path / "localappdata"
    local.mkdir(exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path / "_settings"))
    for key in list(os.environ):
        if key.upper().startswith("SETHLANS_DB_") or key.upper() == "SETHLANS_DATABASE_URL":
            monkeypatch.delenv(key, raising=False)
    for key in ("SETHLANS_DATA_ROOT", "SETHLANS_MANAGER_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _sweep_stale_home_scratch():
    """macOS: remove scratch folders older than a day that killed runs left (``tests._home_scratch``)."""
    if sys.platform == "darwin":
        sweep_stale()


@pytest.fixture
def data_root(tmp_path):
    """A data folder (not yet created) that setup's system-folder check accepts."""
    if sys.platform != "darwin":
        yield tmp_path / "data"
        return
    for holder in scratch_folder():
        yield holder / "data"


@pytest.fixture
def setup_process(tmp_path):
    """Factory: start a real ``run_manager --setup``; all are ended at teardown.

    Starting again with the ``name`` of an ended process reuses its folder
    (its settings, a fresh token), as the launcher does after a Reset.
    """
    started = []

    def start(setup_port=None, extra_env=None, name="proc"):
        folder = tmp_path / name
        folder.mkdir(exist_ok=True)
        proc = SetupProcess(folder)
        started.append(proc)
        return proc.start(setup_port=setup_port, extra_env=extra_env)

    yield start
    for proc in started:
        proc.stop()


@pytest.fixture
def manager_process(tmp_path):
    """Factory: start a real normal ``run_manager`` with ``env``; all are ended at teardown."""
    started = []

    def start(env, name="manager"):
        folder = tmp_path / name
        folder.mkdir()
        proc = ManagerProcess(folder, env)
        started.append(proc)
        return proc.start()

    yield start
    for proc in started:
        proc.stop()


@pytest.fixture(scope="module")
def caddy_cert(tmp_path_factory):
    """One test certificate per module (an RSA 4096 key takes a while)."""
    caddy_binary_or_skip()
    folder = tmp_path_factory.mktemp("caddy")
    cert, key = make_cert(folder)
    return folder, cert, key


@pytest.fixture
def caddy_front(caddy_cert, tmp_path):
    """Factory: a :class:`CaddyFront` with its port picked; ``start(upstream)`` runs it.

    The port is known before the setup process starts, which is told it
    (``SETHLANS_SETUP_PORT``), as the launcher does.
    """
    binary = caddy_binary_or_skip()
    cert_root, cert, key = caddy_cert
    made = []

    def make() -> CaddyFront:
        folder = tmp_path / f"caddy-{len(made)}"
        folder.mkdir()
        front = CaddyFront(binary, folder, cert, key, cert_root)
        made.append(front)
        return front

    yield make
    for front in made:
        front.stop()
