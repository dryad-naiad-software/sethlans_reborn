# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fixtures for the launcher integration tests.

``loop_run`` is the launcher loop in this process with its real children
(``_loop_harness.LoopRun``); it is stopped, and every child it started
ended, at teardown. It needs the pinned Caddy binary and skips without it.
``data_root`` and ``manager_process`` are the setup-mode tests' fixtures.
"""
import pytest

from tests.integration.manager.setup_mode.caddy_front import caddy_binary_or_skip
from tests.integration.manager.setup_mode.conftest import data_root, manager_process  # noqa: F401 - shared fixtures

from ._loop_harness import LoopRun


@pytest.fixture
def loop_run(tmp_path, monkeypatch):
    caddy_binary_or_skip()
    run = LoopRun(tmp_path, monkeypatch)
    yield run
    run.stop()
