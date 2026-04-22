# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.waitress_config`` (Phase 5).

Covers the env-var > ``manager.ini`` > spec-default hierarchy for the
five Waitress tunables and the two Waitress loopback ports. Each tier
is probed independently so a regression only fails the responsible
layer instead of a composite assertion.

Settings-facing helpers (``resolve_public_port_for_settings``,
``resolve_internal_port_for_settings``, ``resolve_ini_path``) live in
``test_waitress_config_settings.py`` to keep both files under the
300-line Python ceiling.
"""

from __future__ import annotations

from sethlans_manager import waitress_config as wc


# ---------------------------------------------------------------------
# Tunables (threads, channel_timeout, connection_limit, max_body)
# ---------------------------------------------------------------------

class TestGetWaitressTuningDefaults:
    """Spec-fixed defaults apply when no env var / ini value is set."""

    def test_threads_default_16(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", raising=False,
        )
        tuning = wc.get_waitress_tuning(tmp_path / "missing.ini")
        assert tuning["threads"] == 16

    def test_channel_timeout_default_300(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_CHANNEL_TIMEOUT", raising=False,
        )
        tuning = wc.get_waitress_tuning(tmp_path / "missing.ini")
        assert tuning["channel_timeout"] == 300

    def test_connection_limit_default_1000(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_CONNECTION_LIMIT", raising=False,
        )
        tuning = wc.get_waitress_tuning(tmp_path / "missing.ini")
        assert tuning["connection_limit"] == 1000

    def test_max_request_body_size_default_10gb(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_MAX_REQUEST_BODY_SIZE",
            raising=False,
        )
        tuning = wc.get_waitress_tuning(tmp_path / "missing.ini")
        assert tuning["max_request_body_size"] == 10 * 1024 * 1024 * 1024


class TestGetWaitressTuningIniOverrides:
    """``manager.ini [server]`` overrides the spec defaults."""

    def _write_ini(self, tmp_path, body):
        ini = tmp_path / "manager.ini"
        ini.write_text(body, encoding="utf-8")
        return ini

    def test_ini_threads_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", raising=False,
        )
        ini = self._write_ini(
            tmp_path, "[server]\nwaitress_threads = 32\n",
        )
        tuning = wc.get_waitress_tuning(ini)
        assert tuning["threads"] == 32

    def test_ini_invalid_int_falls_back_to_default(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", raising=False,
        )
        ini = self._write_ini(
            tmp_path, "[server]\nwaitress_threads = not-a-number\n",
        )
        tuning = wc.get_waitress_tuning(ini)
        assert tuning["threads"] == 16


class TestGetWaitressTuningEnvOverrides:
    """Env vars win over both ini and defaults."""

    def test_env_threads_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", "64",
        )
        tuning = wc.get_waitress_tuning(tmp_path / "missing.ini")
        assert tuning["threads"] == 64

    def test_env_wins_over_ini(self, tmp_path, monkeypatch):
        ini = tmp_path / "manager.ini"
        ini.write_text(
            "[server]\nwaitress_threads = 32\n", encoding="utf-8",
        )
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", "100",
        )
        tuning = wc.get_waitress_tuning(ini)
        assert tuning["threads"] == 100

    def test_env_invalid_int_falls_back_to_ini(
        self, tmp_path, monkeypatch,
    ):
        ini = tmp_path / "manager.ini"
        ini.write_text(
            "[server]\nwaitress_threads = 32\n", encoding="utf-8",
        )
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_THREADS", "garbage",
        )
        tuning = wc.get_waitress_tuning(ini)
        assert tuning["threads"] == 32


# ---------------------------------------------------------------------
# Public port resolution
# ---------------------------------------------------------------------

class TestGetWaitressPublicPort:

    def test_default_8090(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", raising=False,
        )
        assert wc.get_waitress_public_port(
            tmp_path / "missing.ini",
        ) == 8090

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", "9091",
        )
        assert wc.get_waitress_public_port(
            tmp_path / "missing.ini",
        ) == 9091

    def test_ini_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC", raising=False,
        )
        ini = tmp_path / "manager.ini"
        ini.write_text(
            "[server]\nwaitress_loopback_port_public = 9099\n",
            encoding="utf-8",
        )
        assert wc.get_waitress_public_port(ini) == 9099


# ---------------------------------------------------------------------
# Internal port resolution
# ---------------------------------------------------------------------

class TestGetWaitressInternalPort:

    def test_default_8088(self, tmp_path, monkeypatch):
        for n in (
            "SETHLANS_MANAGER_LOOPBACK_PORT",
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
        ):
            monkeypatch.delenv(n, raising=False)
        assert wc.get_waitress_internal_port(
            tmp_path / "missing.ini",
        ) == 8088

    def test_legacy_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", "7777",
        )
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL", "8888",
        )
        # Legacy takes precedence.
        assert wc.get_waitress_internal_port(
            tmp_path / "missing.ini",
        ) == 7777

    def test_new_env_wins_when_legacy_unset(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", raising=False,
        )
        monkeypatch.setenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL", "8989",
        )
        assert wc.get_waitress_internal_port(
            tmp_path / "missing.ini",
        ) == 8989

    def test_ini_new_key_wins_over_legacy_key(
        self, tmp_path, monkeypatch,
    ):
        for n in (
            "SETHLANS_MANAGER_LOOPBACK_PORT",
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
        ):
            monkeypatch.delenv(n, raising=False)
        ini = tmp_path / "manager.ini"
        ini.write_text(
            "[server]\n"
            "waitress_loopback_port_internal = 6001\n"
            "loopback_port = 6002\n",
            encoding="utf-8",
        )
        assert wc.get_waitress_internal_port(ini) == 6001

    def test_ini_legacy_key_fallback(self, tmp_path, monkeypatch):
        for n in (
            "SETHLANS_MANAGER_LOOPBACK_PORT",
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
        ):
            monkeypatch.delenv(n, raising=False)
        ini = tmp_path / "manager.ini"
        ini.write_text(
            "[server]\nloopback_port = 6002\n", encoding="utf-8",
        )
        assert wc.get_waitress_internal_port(ini) == 6002
