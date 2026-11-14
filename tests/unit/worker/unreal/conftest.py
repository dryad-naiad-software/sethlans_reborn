# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures for the Unreal-foundation worker unit tests."""
import pytest

from sethlans_worker_agent.unreal_executor import job_dir

from ..executors._fakes import install_fake_unreal, reset_chunk3_state


@pytest.fixture(autouse=True)
def _reset_chunk3_state():
    reset_chunk3_state()
    job_dir.reset_for_tests()
    yield
    reset_chunk3_state()
    job_dir.reset_for_tests()


@pytest.fixture()
def fake_unreal(monkeypatch):
    return install_fake_unreal(monkeypatch)
