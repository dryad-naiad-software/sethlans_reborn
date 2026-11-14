# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures for the Unreal executor integration tests (fake engine, real processes on Windows)."""
import pytest

from sethlans_worker_agent.unreal_executor import isolation_state, job_dir, startup_recovery


@pytest.fixture(autouse=True)
def _reset_unreal_process_state():
    startup_recovery.reset_for_tests()
    isolation_state.reset_for_tests()
    job_dir.reset_for_tests()
    yield
    startup_recovery.reset_for_tests()
    isolation_state.reset_for_tests()
    job_dir.reset_for_tests()
