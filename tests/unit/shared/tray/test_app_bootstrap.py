# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/app.py`` bootstrap wiring (Phase 8).

Covers topology gating, section construction, signal connection types
(QueuedConnection per FR-6), and the notify closure passed into
``ManagerSection``.  The shutdown-sequence tests live in
``test_app_shutdown.py`` to keep each file under the 300-line cap.

Tests mock the heavy dependencies (QApplication.exec, QSystemTrayIcon,
QTimer, topology, ManagerSection, WorkerSection, QtStatePoller,
icons, notifications) so they run under
``QT_QPA_PLATFORM=offscreen`` without spawning a real tray icon and
without depending on any real ``manager.ini`` / filesystem topology.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for app")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtCore import Qt  # noqa: E402

from shared.tray import app  # noqa: E402
from shared.tray import topology as topo_mod  # noqa: E402
from shared.tray.notifications import NotificationEvent  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _patch_main_deps(mocker, topology, tmp_path):
    """Patch everything ``app.main`` reaches at module scope.

    Returns a dict of the key mocks so tests can assert against them.
    Callers MUST request the ``qapp`` fixture so a real QApplication
    exists for any ``QMenu`` / ``QAction`` construction that happens
    inside ``_build_menu`` (the section mocks return real ``QMenu``
    instances via ``build_qmenu.side_effect=_fake_menu`` — otherwise
    PySide6's QMenu iteration hangs without a live QApplication).
    """
    from PySide6.QtWidgets import QMenu

    # Path layer — avoid touching the real filesystem topology.
    mocker.patch.object(
        app, "get_shared_data_dir", return_value=tmp_path,
    )
    mocker.patch.object(
        app, "get_data_dir",
        side_effect=lambda role: tmp_path / role,
    )
    mocker.patch.object(
        topo_mod, "read_topology", return_value=topology,
    )
    mocker.patch.object(app, "_read_manager_ports",
                        return_value=("localhost", 8080, 8088))
    mocker.patch.object(app, "_configure_logging")
    mocker.patch.object(app.launcher_watch, "init")

    # Section + poller classes — instances capture the constructor args.
    mgr_cls = mocker.patch.object(app, "ManagerSection")
    wk_cls = mocker.patch.object(app, "WorkerSection")
    poller_cls = mocker.patch.object(app, "QtStatePoller")

    # Make build_qmenu return a real (empty) QMenu so _build_menu's
    # action iteration terminates.  We need a QApplication alive for
    # QMenu construction — the caller's ``qapp`` fixture guarantees it.
    mgr_cls.return_value.build_qmenu.side_effect = (
        lambda parent=None: QMenu(parent)
    )
    wk_cls.return_value.build_qmenu.side_effect = (
        lambda parent=None: QMenu(parent)
    )

    # poller.snapshot_changed / .notification / .launcher_gone need
    # .connect(); PySide6 Signal objects aren't auto-wrapped by Mock.
    poller_instance = poller_cls.return_value
    poller_instance.snapshot_changed.connect = mocker.MagicMock()
    poller_instance.notification.connect = mocker.MagicMock()
    poller_instance.launcher_gone.connect = mocker.MagicMock()
    poller_instance.start = mocker.MagicMock()

    # Qt layer — QApplication / QSystemTrayIcon / QTimer / icons.
    app_cls = mocker.patch.object(app, "QApplication")
    app_inst = app_cls.return_value
    app_inst.exec.return_value = 0
    tray_cls = mocker.patch.object(app, "QSystemTrayIcon")
    tray_inst = tray_cls.return_value
    qicon_cls = mocker.patch.object(app, "QIcon")
    qtimer_cls = mocker.patch.object(app, "QTimer")
    icons_mod = mocker.patch.object(app, "icons")
    icons_mod.get_icon.return_value = mocker.MagicMock(name="pixmap")
    notif_mod = mocker.patch.object(app, "notifications")

    return {
        "mgr_cls": mgr_cls,
        "wk_cls": wk_cls,
        "poller_cls": poller_cls,
        "poller_instance": poller_instance,
        "app_cls": app_cls,
        "app_inst": app_inst,
        "tray_cls": tray_cls,
        "tray_inst": tray_inst,
        "qicon_cls": qicon_cls,
        "qtimer_cls": qtimer_cls,
        "icons_mod": icons_mod,
        "notif_mod": notif_mod,
    }


# ------------------------------------------------------------------ #
# Topology gating
# ------------------------------------------------------------------ #

class TestTopologyGating:

    def test_manager_only_builds_only_manager_section(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_MANAGER, tmp_path)
        app.main()
        assert m["mgr_cls"].call_count == 1
        assert m["wk_cls"].call_count == 0
        # Poller start queued via QTimer.singleShot(0, poller.start).
        m["qtimer_cls"].singleShot.assert_called_once()
        args, _ = m["qtimer_cls"].singleShot.call_args
        assert args[0] == 0
        assert args[1] is m["poller_instance"].start

    def test_worker_only_builds_only_worker_section_include_about_true(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_WORKER, tmp_path)
        app.main()
        assert m["wk_cls"].call_count == 1
        assert m["mgr_cls"].call_count == 0
        _, kwargs = m["wk_cls"].call_args
        assert kwargs.get("include_about") is True
        # Worker-only: no manager to poll, so singleShot must NOT be
        # called to start the poller.
        m["qtimer_cls"].singleShot.assert_not_called()

    def test_both_topology_builds_manager_and_worker_include_about_false(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_BOTH, tmp_path)
        app.main()
        assert m["mgr_cls"].call_count == 1
        assert m["wk_cls"].call_count == 1
        _, wk_kwargs = m["wk_cls"].call_args
        # Manager owns About when both sections are present.
        assert wk_kwargs.get("include_about") is False
        # Poller is started (manager present).
        m["qtimer_cls"].singleShot.assert_called_once()


# ------------------------------------------------------------------ #
# Menu construction — separator between sections when both present
# ------------------------------------------------------------------ #

class TestMenuSeparator:

    def test_both_sections_menu_has_separator(self, qapp, tmp_path, mocker):
        """Direct ``_build_menu`` test with lightweight fakes.

        Avoids patching out ManagerSection/WorkerSection so we can
        exercise the real QMenu action merge.  Each section's
        ``build_qmenu`` returns a tiny fake QMenu with one QAction.
        """
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        ctx = mocker.MagicMock()

        def _mgr_menu(parent=None):
            m = QMenu(parent)
            m.addAction("manager-action")
            return m

        def _wk_menu(parent=None):
            m = QMenu(parent)
            m.addAction("worker-action")
            return m

        ctx.manager_section.build_qmenu.side_effect = _mgr_menu
        ctx.worker_section.build_qmenu.side_effect = _wk_menu

        root = app._build_menu(ctx)

        actions = root.actions()
        texts = [a.text() for a in actions if not a.isSeparator()]
        seps = [a for a in actions if a.isSeparator()]
        assert texts == ["manager-action", "worker-action"]
        assert len(seps) == 1
        # Ensure the separator sits between the two groups.
        assert actions[0].text() == "manager-action"
        assert actions[-1].text() == "worker-action"
        # Sanity: each QAction is still usable.
        for a in actions:
            assert isinstance(a, QAction)


# ------------------------------------------------------------------ #
# Signal wiring uses QueuedConnection (FR-6)
# ------------------------------------------------------------------ #

class TestQueuedConnection:

    def test_connect_calls_use_queued_connection(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_BOTH, tmp_path)
        app.main()
        poller = m["poller_instance"]
        # Each of the three signals must connect with QueuedConnection.
        for sig_name in (
            "snapshot_changed", "notification", "launcher_gone",
        ):
            sig = getattr(poller, sig_name)
            assert sig.connect.called, (
                f"{sig_name}.connect() was not called"
            )
            args, kwargs = sig.connect.call_args
            # Accept either (slot, Qt.QueuedConnection) positional or
            # type=Qt.QueuedConnection kwarg.
            if len(args) >= 2:
                ctype = args[1]
            else:
                ctype = kwargs.get("type")
            assert ctype == Qt.QueuedConnection, (
                f"{sig_name} connected without QueuedConnection"
            )


# ------------------------------------------------------------------ #
# Notify closure is passed into ManagerSection
# ------------------------------------------------------------------ #

class TestNotifyClosure:

    def test_manager_section_receives_callable_notify(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_MANAGER, tmp_path)
        app.main()
        _, kwargs = m["mgr_cls"].call_args
        notify = kwargs.get("notify")
        assert notify is not None
        assert callable(notify)

    def test_notify_routes_to_notifications_dispatch(
        self, qapp, mocker, tmp_path,
    ):
        m = _patch_main_deps(mocker, topo_mod.TOPOLOGY_MANAGER, tmp_path)
        app.main()
        _, kwargs = m["mgr_cls"].call_args
        notify = kwargs["notify"]

        evt = NotificationEvent(title="t", message="m")
        notify(evt)
        m["notif_mod"].dispatch.assert_called_once_with(
            m["tray_inst"], evt,
        )
