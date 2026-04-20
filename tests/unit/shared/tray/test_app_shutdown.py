# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shutdown-sequence tests for ``shared/tray/app.py`` (Phase 8).

Covers the spec "Shutdown sequence": ``stop_event.set()`` →
``poller.join(timeout=3.0)`` → ``QObject.disconnect(poller)`` (static
form only — zero-arg instance form raises in PySide6).  Also exercises
``main`` with a faked ``QApplication.exec`` to verify the try/finally
path under ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for app")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from shared.tray import app  # noqa: E402
from shared.tray import topology as topo_mod  # noqa: E402
from shared.tray.notifications import NotificationEvent  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

class _FakeEvent:
    def __init__(self, calls, raises=None):
        self._calls, self._raises = calls, raises

    def set(self):
        self._calls.append("stop_event.set")
        if self._raises is not None:
            raise self._raises


class _FakePoller:
    def __init__(self, calls, join_raises=None):
        self._calls, self._join_raises = calls, join_raises

    def join(self, timeout=None):
        self._calls.append(("poller.join", timeout))
        if self._join_raises is not None:
            raise self._join_raises


class _RecordingCtx:
    """Minimal ``_TrayContext`` stand-in that records shutdown calls."""

    def __init__(self, calls, poller_join_raises=None,
                 stop_set_raises=None):
        self.stop_event = _FakeEvent(calls, stop_set_raises)
        self.poller = _FakePoller(calls, poller_join_raises)


# ------------------------------------------------------------------ #
# Shutdown order
# ------------------------------------------------------------------ #

class TestShutdownOrder:

    def test_runs_three_steps_in_documented_order(self, mocker):
        calls = []
        ctx = _RecordingCtx(calls)
        mocker.patch.object(
            app.QObject, "disconnect",
            side_effect=lambda o: calls.append(("QObject.disconnect", o)),
        )
        app._shutdown(ctx)
        assert calls[0] == "stop_event.set"
        assert calls[1] == ("poller.join", 3.0)
        assert calls[2] == ("QObject.disconnect", ctx.poller)

    def test_disconnect_uses_static_form_not_instance_method(self, mocker):
        """Spec requires ``QObject.disconnect(poller)`` (static form);
        ``poller.disconnect()`` raises ``TypeError`` in PySide6."""
        ctx = _RecordingCtx([])
        spy = mocker.patch.object(app.QObject, "disconnect")
        app._shutdown(ctx)
        spy.assert_called_once_with(ctx.poller)

    def test_shutdown_noop_when_poller_none(self, mocker):
        ctx = _RecordingCtx([])
        ctx.poller = None
        mocker.patch.object(app.QObject, "disconnect")
        app._shutdown(ctx)  # must not raise


# ------------------------------------------------------------------ #
# Shutdown resilience — per-step failures
# ------------------------------------------------------------------ #

class TestShutdownResilience:

    def test_stop_event_set_failure_does_not_skip_poller_join(self, mocker):
        calls = []
        ctx = _RecordingCtx(calls, stop_set_raises=RuntimeError("oops"))
        mocker.patch.object(app.QObject, "disconnect")
        app._shutdown(ctx)
        assert any(isinstance(c, tuple) and c[0] == "poller.join"
                   for c in calls)

    def test_poller_join_failure_does_not_skip_disconnect(self, mocker):
        ctx = _RecordingCtx([], poller_join_raises=RuntimeError("x"))
        spy = mocker.patch.object(app.QObject, "disconnect")
        app._shutdown(ctx)
        spy.assert_called_once_with(ctx.poller)

    def test_disconnect_failure_is_swallowed(self, mocker):
        ctx = _RecordingCtx([])
        mocker.patch.object(
            app.QObject, "disconnect",
            side_effect=RuntimeError("disconnect failed"),
        )
        app._shutdown(ctx)  # must not raise

    def test_shutdown_logs_exceptions_via_logger(self, mocker, caplog):
        import logging
        ctx = _RecordingCtx([], poller_join_raises=RuntimeError("x"))
        mocker.patch.object(app.QObject, "disconnect")
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            app._shutdown(ctx)
        assert any(
            "poller.join" in rec.getMessage().lower()
            or "shutdown" in rec.getMessage().lower()
            for rec in caplog.records
        )


# ------------------------------------------------------------------ #
# main() try/finally — shutdown runs after exec returns and even on raise
# ------------------------------------------------------------------ #

def _patch_main_for_shutdown(mocker, tmp_path,
                             topology=topo_mod.TOPOLOGY_MANAGER):
    """Smaller ``main`` patch set focused on shutdown-path tests."""
    from PySide6.QtWidgets import QMenu

    mocker.patch.object(app, "get_shared_data_dir",
                        return_value=tmp_path)
    mocker.patch.object(app, "get_data_dir",
                        side_effect=lambda r: tmp_path / r)
    mocker.patch.object(topo_mod, "read_topology", return_value=topology)
    mocker.patch.object(app, "_read_manager_ports",
                        return_value=("h", 1, 2))
    mocker.patch.object(app, "_configure_logging")
    mocker.patch.object(app.launcher_watch, "init")
    mgr_cls = mocker.patch.object(app, "ManagerSection")
    wk_cls = mocker.patch.object(app, "WorkerSection")
    mgr_cls.return_value.build_qmenu.side_effect = (
        lambda parent=None: QMenu(parent))
    wk_cls.return_value.build_qmenu.side_effect = (
        lambda parent=None: QMenu(parent))
    poller_cls = mocker.patch.object(app, "QtStatePoller")
    poller_inst = poller_cls.return_value
    for sig in ("snapshot_changed", "notification", "launcher_gone"):
        getattr(poller_inst, sig).connect = mocker.MagicMock()
    poller_inst.start = mocker.MagicMock()
    poller_inst.join = mocker.MagicMock()
    app_cls = mocker.patch.object(app, "QApplication")
    tray_cls = mocker.patch.object(app, "QSystemTrayIcon")
    mocker.patch.object(app, "QIcon")
    mocker.patch.object(app, "QTimer")
    mocker.patch.object(app, "icons")
    mocker.patch.object(app, "notifications")
    return {"app_inst": app_cls.return_value,
            "poller_inst": poller_inst,
            "tray_inst": tray_cls.return_value}


class TestMainTryFinally:

    def test_shutdown_runs_after_exec_returns(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_for_shutdown(mocker, tmp_path)
        m["app_inst"].exec.return_value = 0
        shutdown_spy = mocker.patch.object(app, "_shutdown")

        app.main()

        shutdown_spy.assert_called_once()

    def test_shutdown_runs_even_if_exec_raises(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_for_shutdown(mocker, tmp_path)
        m["app_inst"].exec.side_effect = RuntimeError("boom")
        shutdown_spy = mocker.patch.object(app, "_shutdown")

        with pytest.raises(RuntimeError, match="boom"):
            app.main()

        shutdown_spy.assert_called_once()

    def test_shutdown_invoked_with_ctx(self, qapp, mocker, tmp_path):
        m = _patch_main_for_shutdown(mocker, tmp_path)
        m["app_inst"].exec.return_value = 0
        shutdown_spy = mocker.patch.object(app, "_shutdown")

        app.main()
        (args, _) = shutdown_spy.call_args
        # The ctx passed must be the same ``_TrayContext`` instance the
        # bootstrap constructed (the only positional arg).
        assert isinstance(args[0], app._TrayContext)


# ------------------------------------------------------------------ #
# Signal-slot wiring — drive slots directly via mocks
# ------------------------------------------------------------------ #

class TestConnectedSlotBehavior:

    def test_snapshot_slot_refreshes_icon_and_sections(
        self, qapp, mocker, tmp_path,
    ):
        from shared.tray.poller import ManagerSnapshot
        m = _patch_main_for_shutdown(mocker, tmp_path,
                                     topology=topo_mod.TOPOLOGY_BOTH)
        m["app_inst"].exec.return_value = 0
        mocker.patch.object(app, "_shutdown")
        refresh_icon = mocker.patch.object(app, "_refresh_icon")
        refresh_sections = mocker.patch.object(app, "_refresh_sections")

        app.main()

        slot = m["poller_inst"].snapshot_changed.connect.call_args[0][0]
        slot(ManagerSnapshot(state="running", boot_id="b1"))
        assert refresh_icon.call_count == 1
        assert refresh_sections.call_count == 1

    def test_notification_slot_dispatches_event(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_for_shutdown(mocker, tmp_path)
        m["app_inst"].exec.return_value = 0
        mocker.patch.object(app, "_shutdown")

        app.main()

        poller = m["poller_inst"]
        slot = poller.notification.connect.call_args[0][0]
        evt = NotificationEvent(title="t", message="m")
        slot(evt)

        app.notifications.dispatch.assert_called_once_with(
            m["tray_inst"], evt,
        )

    def test_launcher_gone_is_connected_to_app_quit(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_for_shutdown(mocker, tmp_path)
        m["app_inst"].exec.return_value = 0
        mocker.patch.object(app, "_shutdown")

        app.main()

        poller = m["poller_inst"]
        # The slot connected to launcher_gone must be app.quit.
        connected_slot = poller.launcher_gone.connect.call_args[0][0]
        assert connected_slot is m["app_inst"].quit


# ------------------------------------------------------------------ #
# _shutdown never propagates — sanity
# ------------------------------------------------------------------ #

class TestShutdownNeverRaises:

    def test_all_three_steps_fail_no_exception_escapes(self, mocker):
        calls = []
        ctx = _RecordingCtx(
            calls, stop_set_raises=RuntimeError("a"),
            poller_join_raises=RuntimeError("b"),
        )
        mocker.patch.object(app.QObject, "disconnect",
                            side_effect=RuntimeError("c"))
        app._shutdown(ctx)  # must not raise

    def test_threading_event_real_instance_still_works(self, mocker):
        stop = threading.Event()
        ctx = _RecordingCtx([])
        ctx.stop_event = stop
        mocker.patch.object(app.QObject, "disconnect")
        app._shutdown(ctx)
        assert stop.is_set()
