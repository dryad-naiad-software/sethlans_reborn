# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``_publish_broadcaster_params`` and its container opt-out (#296).

Natively the manager writes ``broadcaster_params.json`` for the
launcher's broadcaster. In the Docker image no launcher broadcaster
runs and the manager's code dir is root-owned, so the container
entrypoint sets ``SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS=0`` and
the manager must write nothing there.
"""

from __future__ import annotations

import json

import pytest

from sethlans_manager import runtime_init, runtime_state

ENV = "SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS"


@pytest.fixture
def base_dir(settings, tmp_path, monkeypatch):
    """Source mode with BASE_DIR in tmp_path and known runtime state."""
    monkeypatch.delenv(ENV, raising=False)
    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(runtime_state, "manager_id", "mid-1", raising=False)
    monkeypatch.setattr(runtime_state, "manager_boot_id", "boot-1", raising=False)
    monkeypatch.setattr(runtime_state, "broadcaster_port", 8080, raising=False)
    return tmp_path


def test_env_name_matches_module_constant():
    assert runtime_init.PUBLISH_BROADCASTER_PARAMS_ENV == ENV


def test_publishes_by_default(base_dir):
    runtime_init._publish_broadcaster_params()

    data = json.loads((base_dir / "broadcaster_params.json").read_text())
    assert data["manager_id"] == "mid-1"
    assert data["manager_boot_id"] == "boot-1"
    assert data["port"] == 8080


@pytest.mark.parametrize("value", ["1", "true", "yes", ""])
def test_publishes_for_other_values(base_dir, monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    runtime_init._publish_broadcaster_params()
    assert (base_dir / "broadcaster_params.json").is_file()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_opt_out_writes_nothing(base_dir, monkeypatch, value):
    monkeypatch.setenv(ENV, value)

    def _no_mkstemp(*_a, **_k):
        raise AssertionError("mkstemp must not run when publishing is off")

    monkeypatch.setattr("tempfile.mkstemp", _no_mkstemp)
    runtime_init._publish_broadcaster_params()

    assert list(base_dir.iterdir()) == []
