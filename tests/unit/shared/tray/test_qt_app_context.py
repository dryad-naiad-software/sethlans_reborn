# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_app.py`` context + helper functions.

Split out from the bootstrap-wiring tests so each file stays under the
300-line cap.  Covers:

* ``_TrayContext`` topology flag / snapshot / icon-state invariants.
* ``_read_manager_ports`` INI parsing.
* ``_refresh_icon`` / ``_refresh_sections`` direct-slot behavior.
* ``shared/run_tray.py`` entrypoint flip (FR-7).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_app")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from shared.tray import qt_app  # noqa: E402
from shared.tray import topology as topo_mod  # noqa: E402
from shared.tray.qt_poller import ManagerSnapshot  # noqa: E402


# ------------------------------------------------------------------ #
# _refresh_icon / _refresh_sections — direct slot tests
# ------------------------------------------------------------------ #

class TestRefreshHelpers:

    def test_refresh_icon_calls_get_icon_and_set_icon(
        self, qapp, mocker,
    ):
        tray = mocker.MagicMock()
        ctx = mocker.MagicMock()
        ctx.icon_states.return_value = ("running", None)
        pixmap = mocker.MagicMock(name="pixmap")
        get_icon = mocker.patch.object(
            qt_app.qt_icons, "get_icon", return_value=pixmap,
        )
        qicon = mocker.patch.object(qt_app, "QIcon")

        qt_app._refresh_icon(ctx, tray)

        get_icon.assert_called_once_with("running", None)
        qicon.assert_called_once_with(pixmap)
        tray.setIcon.assert_called_once_with(qicon.return_value)

    def test_refresh_icon_swallows_exceptions(self, qapp, mocker):
        tray = mocker.MagicMock()
        ctx = mocker.MagicMock()
        ctx.icon_states.side_effect = RuntimeError("boom")
        # Must not propagate.
        qt_app._refresh_icon(ctx, tray)
        tray.setIcon.assert_not_called()

    def test_refresh_sections_calls_both_when_present(self, mocker):
        ctx = mocker.MagicMock()
        qt_app._refresh_sections(ctx)
        ctx.manager_section.refresh.assert_called_once_with()
        ctx.worker_section.refresh.assert_called_once_with()

    def test_refresh_sections_skips_none(self, mocker):
        ctx = mocker.MagicMock()
        ctx.manager_section = None
        qt_app._refresh_sections(ctx)
        ctx.worker_section.refresh.assert_called_once_with()

    def test_refresh_sections_logs_and_continues_on_error(self, mocker):
        ctx = mocker.MagicMock()
        ctx.manager_section.refresh.side_effect = RuntimeError("mgr fail")
        # Must not propagate and worker refresh must still run.
        qt_app._refresh_sections(ctx)
        ctx.worker_section.refresh.assert_called_once_with()


# ------------------------------------------------------------------ #
# _TrayContext shape
# ------------------------------------------------------------------ #

def _patch_ctx_deps(mocker, tmp_path, topology):
    mocker.patch.object(qt_app, "get_shared_data_dir",
                        return_value=tmp_path)
    mocker.patch.object(qt_app, "get_data_dir",
                        side_effect=lambda r: tmp_path / r)
    mocker.patch.object(topo_mod, "read_topology",
                        return_value=topology)
    mocker.patch.object(qt_app, "_read_manager_ports",
                        return_value=("h", 1, 2))


class TestTrayContext:

    def test_context_topology_flags_manager_only(self, mocker, tmp_path):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_MANAGER)
        ctx = qt_app._TrayContext()
        assert ctx.wants_manager is True
        assert ctx.wants_worker is False
        assert isinstance(ctx.stop_event, threading.Event)
        assert isinstance(ctx.quit_flag, threading.Event)
        assert ctx.loopback_url.endswith(":2/api/status/public/")

    def test_context_topology_flags_worker_only(self, mocker, tmp_path):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_WORKER)
        ctx = qt_app._TrayContext()
        assert ctx.wants_manager is False
        assert ctx.wants_worker is True

    def test_context_topology_flags_both(self, mocker, tmp_path):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_BOTH)
        ctx = qt_app._TrayContext()
        assert ctx.wants_manager is True
        assert ctx.wants_worker is True

    def test_current_snapshot_without_poller_returns_default(
        self, mocker, tmp_path,
    ):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_BOTH)
        ctx = qt_app._TrayContext()
        snap = ctx.current_snapshot()
        assert isinstance(snap, ManagerSnapshot)
        assert snap.state == "starting"

    def test_icon_states_returns_none_for_absent_role(self, mocker, tmp_path):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_MANAGER)
        ctx = qt_app._TrayContext()
        mgr, wk = ctx.icon_states()
        assert mgr == "starting"
        assert wk is None

    def test_icon_states_worker_only_has_idle_worker(self, mocker, tmp_path):
        _patch_ctx_deps(mocker, tmp_path, topo_mod.TOPOLOGY_WORKER)
        ctx = qt_app._TrayContext()
        mgr, wk = ctx.icon_states()
        assert mgr is None
        assert wk == "idle"


# ------------------------------------------------------------------ #
# _read_manager_ports — no manager.ini fallback
# ------------------------------------------------------------------ #

class TestReadManagerPorts:

    def test_defaults_when_ini_missing(self, tmp_path):
        host, main, lb = qt_app._read_manager_ports(tmp_path)
        assert host == "localhost"
        assert main == 8080
        assert lb == 8088

    def test_reads_values_from_ini(self, tmp_path):
        (tmp_path / "manager.ini").write_text(
            "[server]\nport = 9000\nloopback_port = 9001\n",
            encoding="utf-8",
        )
        _host, main, lb = qt_app._read_manager_ports(tmp_path)
        assert main == 9000
        assert lb == 9001

    def test_malformed_ini_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "manager.ini").write_text(
            "not a valid ini file [[[",
            encoding="utf-8",
        )
        _host, main, lb = qt_app._read_manager_ports(tmp_path)
        assert main == 8080
        assert lb == 8088


# ------------------------------------------------------------------ #
# Entrypoint flip — shared.run_tray delegates to qt_app.main
# ------------------------------------------------------------------ #

class TestRunTrayEntrypoint:

    def test_run_tray_calls_qt_app_main(self, mocker):
        import shared.run_tray as rt
        import signal as signal_mod
        mocker.patch.object(signal_mod, "signal")
        spy = mocker.patch.object(qt_app, "main")
        rc = rt.main()
        spy.assert_called_once_with()
        assert rc == 0

    def test_run_tray_file_exists(self):
        # Sanity: the flipped entrypoint module is importable.
        import shared.run_tray
        assert hasattr(shared.run_tray, "main")

    def test_run_tray_imports_qt_app(self):
        import shared.run_tray as rt
        # The Phase 8 flip routes through shared.tray.qt_app.
        src = Path(rt.__file__).read_text(encoding="utf-8")
        assert "qt_app" in src
