# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared.frozen_paths.get_shared_data_dir``.

Added for the tray-helper-unified spec: the shared data directory holds
the setup sentinel, ``topology.json``, and the IPC marker files — the
tray + launcher both need to resolve it consistently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.frozen_paths import get_data_dir, get_shared_data_dir


class TestGetSharedDataDir:

    def test_env_override_is_honored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        assert get_shared_data_dir() == tmp_path

    def test_relative_env_override_raises(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_DATA_DIR", "relative/path")
        with pytest.raises(ValueError, match="absolute"):
            get_shared_data_dir()

    def test_default_is_parent_of_manager_data_dir(self, monkeypatch):
        # Ensure no explicit env override for shared.
        monkeypatch.delenv("SETHLANS_DATA_DIR", raising=False)
        # Remove per-component override if set, then derive.
        monkeypatch.delenv("SETHLANS_MANAGER_DATA_DIR", raising=False)
        result = get_shared_data_dir()
        manager_data = get_data_dir("manager")
        assert result == manager_data.parent

    def test_returns_path_instance(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SETHLANS_DATA_DIR", str(tmp_path))
        assert isinstance(get_shared_data_dir(), Path)
