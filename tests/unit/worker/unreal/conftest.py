# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures for the Unreal-foundation worker unit tests."""
import pytest

from sethlans_worker_agent.unreal_executor import job_dir, reserve_local

from ..executors._fakes import install_fake_unreal, reset_chunk3_state


@pytest.fixture(autouse=True)
def _reset_chunk3_state():
    reset_chunk3_state()
    job_dir.reset_for_tests()
    yield
    reset_chunk3_state()
    job_dir.reset_for_tests()


@pytest.fixture(autouse=True)
def _plenty_of_ram(monkeypatch):
    """The RAM gate's real readers (host RAM; Windows-only commit headroom) give room for any job.

    Tests of the gate itself pass their own ``ReserveDeps.ram_readers`` (render per card FR-PC-RAM-6).
    """
    monkeypatch.setattr(reserve_local, 'available_mb', lambda: 1 << 20)
    monkeypatch.setattr(reserve_local, 'commit_headroom_mb', lambda: 1 << 20)


@pytest.fixture()
def fake_unreal(monkeypatch):
    return install_fake_unreal(monkeypatch)
