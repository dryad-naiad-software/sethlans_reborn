# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/app.py``: tray bootstrap and shutdown (FR-LN-4, issue #106)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from shared.tray import app  # noqa: E402


@pytest.fixture
def main_deps(mocker, settings_dir):
    mocker.patch.object(app, "_configure_logging")
    mocker.patch.object(app.launcher_watch, "init")
    deps = {
        "qapp": mocker.patch.object(app, "QApplication"),
        "tray": mocker.patch.object(app, "QSystemTrayIcon"),
        "poller": mocker.patch.object(app, "QtStatePoller"),
        "timer": mocker.patch.object(app, "QTimer"),
        "install": mocker.patch.object(app.app_phase, "install"),
        "shutdown": mocker.patch.object(app, "_shutdown"),
    }
    return deps


def test_main_starts_the_poller_and_state_timer_in_every_mode(main_deps, settings_dir):
    """6c: the state timer follows the launcher, and the poller (which watches the
    launcher process) runs in every mode, so a worker-only tray also exits with it."""
    app.main()
    started = [c.args[1] for c in main_deps["timer"].singleShot.call_args_list]
    assert started == [main_deps["poller"].return_value.start, main_deps["timer"].return_value.start]
    main_deps["timer"].return_value.setInterval.assert_called_once_with(app.STATE_POLL_MS)
    main_deps["install"].assert_called_once()
    main_deps["shutdown"].assert_called_once()


def test_main_brands_the_application(main_deps):
    """Issue #106: taskbar / Alt+Tab read "Sethlans"."""
    app.main()
    main_deps["qapp"].return_value.setApplicationDisplayName.assert_called_once_with("Sethlans")


def test_shutdown_runs_even_if_exec_raises(main_deps):
    """The poller is always stopped, whatever ends the event loop."""
    main_deps["qapp"].return_value.exec.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        app.main()
    main_deps["shutdown"].assert_called_once()


def test_poller_signals_are_queued_to_the_gui_thread(mocker):
    """6c review F3: the poller thread's signals reach the GUI thread through
    ``Qt.QueuedConnection``, and ``launcher_gone`` quits the tray (FR-19c)."""
    ctx, tray, qt_app = mocker.Mock(), mocker.Mock(), mocker.Mock()
    app._connect_poller(ctx, tray, qt_app)
    ((snapshot_slot,), snapshot_kw) = ctx.poller.snapshot_changed.connect.call_args
    ((gone_slot,), gone_kw) = ctx.poller.launcher_gone.connect.call_args
    assert snapshot_kw == gone_kw == {"type": app.Qt.QueuedConnection}
    assert gone_slot is qt_app.quit
    assert callable(snapshot_slot)


def test_shutdown_order_and_static_disconnect(mocker):
    """Shutdown: timer stop, stop_event, join, then the static QObject.disconnect
    (the instance form raises in PySide6); every step runs even if one fails."""
    calls = []
    ctx = mocker.Mock()
    timer = mocker.Mock()

    def stop_fails():
        calls.append("timer")
        raise RuntimeError("timer gone")

    timer.stop.side_effect = stop_fails
    ctx.stop_event.set.side_effect = lambda: calls.append("stop")
    ctx.poller.join.side_effect = lambda timeout: calls.append(("join", timeout))
    disconnect = mocker.patch.object(app.QObject, "disconnect", side_effect=lambda p: calls.append("disconnect"))
    app._shutdown(ctx, timer)
    assert calls == ["timer", "stop", ("join", 3.0), "disconnect"]
    disconnect.assert_called_once_with(ctx.poller)


@pytest.mark.parametrize(("body", "port"), [
    (None, 8088),
    ("[server]\nwaitress_loopback_port_internal = 9101\nloopback_port = 9102\n", 9101),
    ("[server]\nloopback_port = 9102\n", 9102),
    ("[server]\nloopback_port = abc\n", 8088),
    ("[server]\nloopback_port = 0\n", 8088),
    ("[server]\nloopback_port = 65536\n", 8088),
])
def test_loopback_port(tmp_path, body, port):
    """The poller asks the manager's internal listener (the manager's own key order), else
    8088; a value that is not a port number (1..65535) is not used (security review L3)."""
    ini = tmp_path / "sethlans.ini"
    if body is not None:
        ini.write_text(body, encoding="utf-8")
    assert app.read_loopback_port(ini) == port
