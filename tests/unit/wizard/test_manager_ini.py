# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/manager_ini.py``
(FR-M2-INI)."""

from __future__ import annotations

import configparser

from wizard.sethlans_wizard import manager_ini


class TestUpdateManagerIni:

    def test_creates_new_file(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "127.0.0.1", "bind_port": 8080},
        )
        target = tmp_path / "manager.ini"
        assert target.exists()
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 8080

    def test_preserves_unrelated_sections(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
        )
        manager_ini.update_manager_ini(
            tmp_path, "database",
            {"engine": "sqlite", "name": "sethlans.db"},
        )
        target = tmp_path / "manager.ini"
        parser = configparser.ConfigParser()
        parser.read(str(target))
        # Both sections must survive the multi-write under FR-M2-INI's lock.
        assert parser.get("server", "bind_host") == "0.0.0.0"
        assert parser.get("database", "engine") == "sqlite"

    def test_section_overwrite_idempotent(self, tmp_path):
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
        )
        manager_ini.update_manager_ini(
            tmp_path, "server",
            {"bind_host": "127.0.0.1", "bind_port": 9000},
        )
        target = tmp_path / "manager.ini"
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"
        assert parser.getint("server", "bind_port") == 9000
