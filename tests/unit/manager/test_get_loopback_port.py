# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``run_manager.get_loopback_port`` (FR-22 / FR-22b)."""

from __future__ import annotations

import run_manager


class TestGetLoopbackPort:

    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("SETHLANS_MANAGER_LOOPBACK_PORT", "9999")
        # manager.ini (if any) must not override the env var.
        assert run_manager.get_loopback_port() == "9999"

    def test_manager_ini_override(self, monkeypatch, tmp_path):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", raising=False,
        )
        ini_path = tmp_path / "manager.ini"
        ini_path.write_text(
            "[server]\nloopback_port = 7777\n", encoding="utf-8",
        )
        monkeypatch.setattr(
            run_manager, "get_config_file_path",
            lambda _d: ini_path,
        )
        assert run_manager.get_loopback_port() == "7777"

    def test_default_8088_when_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", raising=False,
        )
        # Point at a missing ini.
        monkeypatch.setattr(
            run_manager, "get_config_file_path",
            lambda _d: tmp_path / "does-not-exist.ini",
        )
        assert run_manager.get_loopback_port() == "8088"

    def test_env_wins_over_ini(self, monkeypatch, tmp_path):
        ini_path = tmp_path / "manager.ini"
        ini_path.write_text(
            "[server]\nloopback_port = 7777\n", encoding="utf-8",
        )
        monkeypatch.setattr(
            run_manager, "get_config_file_path",
            lambda _d: ini_path,
        )
        monkeypatch.setenv("SETHLANS_MANAGER_LOOPBACK_PORT", "1234")
        assert run_manager.get_loopback_port() == "1234"
