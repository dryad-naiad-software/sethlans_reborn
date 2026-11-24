# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the settings-facing helpers in
``sethlans_manager.waitress_config``.

Split from ``test_waitress_config.py`` to respect the 300-line Python
file ceiling. Covers:

* ``resolve_public_port_for_settings`` / ``resolve_internal_port_for_settings``
  — the env-var > ini hierarchy as consumed by ``settings.py``.
* ``_read_ini`` — the settings file parsed without interpolation.
"""

from __future__ import annotations

import configparser

from sethlans_manager import waitress_config as wc


class TestResolveSettingsHelpers:
    """Ensure the settings-facing resolvers honour the hierarchy."""

    def _cfg(self, text=""):
        cfg = configparser.ConfigParser()
        cfg.read_string(text)
        return cfg

    def test_public_none_when_unset(self, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", raising=False,
        )
        assert wc.resolve_public_port_for_settings(self._cfg()) is None

    def test_public_env_override(self, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", "9091",
        )
        assert wc.resolve_public_port_for_settings(
            self._cfg(),
        ) == 9091

    def test_public_ini_override(self, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", raising=False,
        )
        cfg = self._cfg(
            "[server]\nwaitress_loopback_port_public = 9099\n",
        )
        assert wc.resolve_public_port_for_settings(cfg) == 9099

    def test_internal_default_8088(self, monkeypatch):
        for n in (
            "SETHLANS_MANAGER_LOOPBACK_PORT",
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
        ):
            monkeypatch.delenv(n, raising=False)
        assert wc.resolve_internal_port_for_settings(
            self._cfg(),
        ) == 8088

    def test_internal_legacy_env_wins(self, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", "7001",
        )
        assert wc.resolve_internal_port_for_settings(
            self._cfg(),
        ) == 7001

    def test_internal_ini_new_key(self, monkeypatch):
        for n in (
            "SETHLANS_MANAGER_LOOPBACK_PORT",
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
        ):
            monkeypatch.delenv(n, raising=False)
        cfg = self._cfg(
            "[server]\nwaitress_loopback_port_internal = 6501\n",
        )
        assert wc.resolve_internal_port_for_settings(cfg) == 6501


class TestReadIni:
    """``_read_ini`` parses the settings file without interpolation."""

    def test_percent_value_is_literal(self, tmp_path):
        ini = tmp_path / "sethlans.ini"
        ini.write_text("[server]\nnote = 50% off %(x)s\n", encoding="utf-8")
        assert wc._read_ini(ini).get("server", "note") == "50% off %(x)s"

    def test_missing_file_is_empty(self, tmp_path):
        assert wc._read_ini(tmp_path / "missing.ini").sections() == []

    def test_unparsable_file_warns_and_is_empty(self, tmp_path, capsys):
        ini = tmp_path / "sethlans.ini"
        ini.write_text("no section header\n", encoding="utf-8")
        assert wc._read_ini(ini).sections() == []
        assert "Could not read the settings file" in capsys.readouterr().err
