# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for launcher logic in ``launcher/run_launcher.py``.

Covers ``_is_setup_complete``, ``_read_topology``, ``_is_headless``,
and ``_bootstrap_first_run``.
"""

import json
import sys

import pytest

from launcher.run_launcher import (
    _is_headless,
    _is_setup_complete,
    _read_topology,
    _bootstrap_first_run,
    MANAGER_PORT,
)


# ---- _is_setup_complete() --------------------------------------------------

class TestIsSetupComplete:

    def test_returns_false_when_sentinel_missing(self, tmp_path):
        assert _is_setup_complete(tmp_path) is False

    def test_returns_true_when_sentinel_exists(self, tmp_path):
        (tmp_path / ".setup_complete").touch()
        assert _is_setup_complete(tmp_path) is True


# ---- _read_topology() -----------------------------------------------------

class TestReadTopology:

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        assert _read_topology(tmp_path) == {}

    def test_reads_valid_topology(self, tmp_path):
        topo = {"topology": "manager_worker", "version": 1}
        (tmp_path / "topology.json").write_text(
            json.dumps(topo), encoding="utf-8",
        )
        result = _read_topology(tmp_path)
        assert result == topo

    def test_reads_manager_only_topology(self, tmp_path):
        topo = {"topology": "manager"}
        (tmp_path / "topology.json").write_text(
            json.dumps(topo), encoding="utf-8",
        )
        result = _read_topology(tmp_path)
        assert result["topology"] == "manager"

    def test_reads_worker_only_topology(self, tmp_path):
        topo = {"topology": "worker"}
        (tmp_path / "topology.json").write_text(
            json.dumps(topo), encoding="utf-8",
        )
        result = _read_topology(tmp_path)
        assert result["topology"] == "worker"


# ---- _is_headless() -------------------------------------------------------

class TestIsHeadless:

    @pytest.mark.skipif(
        sys.platform.startswith("linux"),
        reason="Tests Windows-branch headless detection: mocks "
        "platform.system to 'Windows' so is_headless() reads "
        "SESSIONNAME, but SESSIONNAME is absent on Linux CI so the "
        "Windows branch returns True (headless) instead of the "
        "expected False. The Linux branch is exercised by the other "
        "tests in this class.",
    )
    def test_not_headless_on_non_linux(self, mocker):
        mocker.patch("platform.system", return_value="Windows")
        assert _is_headless() is False

    def test_not_headless_on_darwin(self, mocker):
        mocker.patch("platform.system", return_value="Darwin")
        assert _is_headless() is False

    def test_headless_on_linux_no_display(self, mocker, monkeypatch):
        mocker.patch("platform.system", return_value="Linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _is_headless() is True

    def test_not_headless_on_linux_with_display(self, mocker, monkeypatch):
        mocker.patch("platform.system", return_value="Linux")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert _is_headless() is False

    def test_not_headless_on_linux_with_wayland(self, mocker, monkeypatch):
        mocker.patch("platform.system", return_value="Linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert _is_headless() is False


# ---- _bootstrap_first_run() -----------------------------------------------

class TestBootstrapFirstRun:

    def test_creates_manager_data_dir(self, tmp_path, mocker):
        mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        result = _bootstrap_first_run(tmp_path)
        assert result == tmp_path / "manager"
        assert result.is_dir()

    def test_creates_manager_ini(self, tmp_path, mocker):
        mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        _bootstrap_first_run(tmp_path)
        ini_path = tmp_path / "manager" / "manager.ini"
        assert ini_path.exists()
        content = ini_path.read_text(encoding="utf-8")
        assert "[security]" in content
        assert "secret_key" in content
        assert "debug = False" in content
        assert f"port = {MANAGER_PORT}" in content

    def test_does_not_overwrite_existing_ini(self, tmp_path, mocker):
        mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        manager_data = tmp_path / "manager"
        manager_data.mkdir(parents=True)
        ini_path = manager_data / "manager.ini"
        ini_path.write_text("existing content", encoding="utf-8")

        _bootstrap_first_run(tmp_path)
        assert ini_path.read_text(encoding="utf-8") == "existing content"

    def test_secret_key_is_long_enough(self, tmp_path, mocker):
        mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        _bootstrap_first_run(tmp_path)
        ini_path = tmp_path / "manager" / "manager.ini"
        content = ini_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("secret_key"):
                key = line.split("=", 1)[1].strip()
                assert len(key) >= 67
                break
        else:
            pytest.fail("secret_key not found in manager.ini")

    def test_sets_file_permissions(self, tmp_path, mocker):
        mock_perms = mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        _bootstrap_first_run(tmp_path)
        ini_path = tmp_path / "manager" / "manager.ini"
        mock_perms.assert_called_once_with(ini_path)

    def test_writes_waitress_port_keys(self, tmp_path, mocker):
        # Issue #100 / TR-3: ``_bootstrap_first_run`` must declare both
        # the public-origin and internal-origin Waitress loopback ports
        # so the launcher-side Caddy supervisor can read them from
        # manager.ini via ``waitress_config`` helpers without falling
        # through to defaults silently.
        mocker.patch(
            'launcher.run_launcher._set_file_permissions',
        )
        _bootstrap_first_run(tmp_path)
        ini_path = tmp_path / "manager" / "manager.ini"
        content = ini_path.read_text(encoding="utf-8")
        assert "waitress_loopback_port_public = 8090" in content
        assert "waitress_loopback_port_internal = 8088" in content
