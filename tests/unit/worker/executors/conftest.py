# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures for the executor-interface unit tests."""
import pytest

from ._fakes import install_fake_unreal, reset_chunk3_state


@pytest.fixture(autouse=True)
def _reset_chunk3_state():
    reset_chunk3_state()
    yield
    reset_chunk3_state()


@pytest.fixture()
def fake_unreal(monkeypatch):
    return install_fake_unreal(monkeypatch)
