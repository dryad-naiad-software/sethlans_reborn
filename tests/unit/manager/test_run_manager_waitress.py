# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the Phase 5 Waitress launcher (``run_manager`` +
``sethlans_manager.waitress_launcher``).

Scoped to the shared drain helper, signal-handler registration,
listener-thread crash propagation, ``--dev`` fail-fast, and the
backwards-compat ``run_manager.get_loopback_port`` shim. The
dual-listener happy path lives in
``test_run_manager_waitress_launch.py`` to keep both files under the
300-line Python ceiling.
"""

from __future__ import annotations

import pytest

import run_manager
from sethlans_manager import waitress_launcher


# ---------------------------------------------------------------------
# stop_waitress_listeners — shared drain helper.
# ---------------------------------------------------------------------

class TestStopWaitressListeners:

    def setup_method(self):
        # Module-level state is global; reset between tests to avoid
        # cross-contamination.
        waitress_launcher._servers.clear()
        waitress_launcher._threads.clear()
        waitress_launcher._shutdown_event.clear()

    def test_noop_when_lists_empty(self):
        # Must not raise.
        waitress_launcher.stop_waitress_listeners()

    def test_close_and_join_all(self, mocker):
        srv_a = mocker.MagicMock()
        srv_b = mocker.MagicMock()
        th_a = mocker.MagicMock()
        th_a.is_alive.return_value = False
        th_a.name = "manager-waitress-public"
        th_b = mocker.MagicMock()
        th_b.is_alive.return_value = False
        th_b.name = "manager-waitress-loopback"
        waitress_launcher._servers.extend([srv_a, srv_b])
        waitress_launcher._threads.extend([th_a, th_b])

        waitress_launcher.stop_waitress_listeners(join_timeout=2.0)

        srv_a.close.assert_called_once()
        srv_b.close.assert_called_once()
        th_a.join.assert_called_once_with(timeout=2.0)
        th_b.join.assert_called_once_with(timeout=2.0)
        assert waitress_launcher._servers == []
        assert waitress_launcher._threads == []

    def test_thread_alive_warning_goes_to_stderr(self, mocker, capsys):
        srv = mocker.MagicMock()
        th = mocker.MagicMock()
        th.is_alive.return_value = True
        th.name = "manager-waitress-public"
        waitress_launcher._servers.append(srv)
        waitress_launcher._threads.append(th)

        waitress_launcher.stop_waitress_listeners(join_timeout=0.1)

        err = capsys.readouterr().err
        assert "did not exit" in err.lower()


# ---------------------------------------------------------------------
# Signal handler registration.
# ---------------------------------------------------------------------

class TestInstallSignalHandlers:

    def test_installs_sigint_and_sigterm(self, mocker):
        signal_mod = waitress_launcher.signal
        sig_fn = mocker.patch.object(signal_mod, "signal")
        waitress_launcher._install_signal_handlers()
        # SIGINT always; SIGTERM where the platform exposes it (all
        # platforms we support — signal.SIGTERM is defined on win32 too).
        calls = [c.args[0] for c in sig_fn.call_args_list]
        assert signal_mod.SIGINT in calls
        if hasattr(signal_mod, "SIGTERM"):
            assert signal_mod.SIGTERM in calls


# ---------------------------------------------------------------------
# --dev dispatch (Phase 6 replaces the fail-fast stub).
# ---------------------------------------------------------------------

class TestDevFlagDispatch:

    def test_run_manager_dev_invokes_watchdog_and_exits(
        self, mocker, monkeypatch,
    ):
        """``run_manager.main()`` must hand ``--dev`` off to
        ``run_dev_watchdog`` and then ``sys.exit`` with its rc — never
        falling through to ``setup_certificates``."""
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        fake_rdw = mocker.patch(
            "sethlans_manager.dev_watchdog.run_dev_watchdog",
            return_value=0,
        )
        setup_certs = mocker.patch.object(
            run_manager, "setup_certificates",
        )
        with pytest.raises(SystemExit) as excinfo:
            run_manager.main()
        assert excinfo.value.code == 0
        fake_rdw.assert_called_once()
        setup_certs.assert_not_called()

    def test_run_manager_dev_without_watchdog_exits_2(
        self, mocker, monkeypatch, capsys,
    ):
        """If ``watchdog`` is absent we must surface a clear error
        and exit with code 2, not a raw traceback."""
        monkeypatch.delenv("SETHLANS_DEV_IS_PARENT", raising=False)
        mocker.patch.object(
            run_manager.sys, "argv", ["run_manager", "--dev"],
        )
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sethlans_manager.dev_watchdog":
                raise ImportError("No module named 'watchdog'")
            return real_import(name, *args, **kwargs)

        mocker.patch.object(builtins, "__import__", side_effect=fake_import)
        with pytest.raises(SystemExit) as excinfo:
            run_manager.main()
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "watchdog" in err.lower()
        assert "requirements-dev.txt" in err


# ---------------------------------------------------------------------
# Listener-thread crash propagation.
# ---------------------------------------------------------------------

class TestListenerThreadCrashSetsShutdown:

    def setup_method(self):
        waitress_launcher._shutdown_event.clear()

    def test_exception_in_listener_sets_shutdown_event(
        self, mocker, capsys,
    ):
        """If a listener's ``.run()`` raises, the shutdown event must
        flip set so the main thread's ``wait()`` unblocks — otherwise
        a silent crash would leave the process wedged."""
        fake_server = mocker.MagicMock()
        fake_server.run.side_effect = RuntimeError("boom")

        waitress_launcher._listener_thread_main(fake_server, "public")

        assert waitress_launcher._shutdown_event.is_set()
        err = capsys.readouterr().err
        assert "crashed" in err.lower()


# ---------------------------------------------------------------------
# run_manager.get_loopback_port — preserved for backwards compat.
# ---------------------------------------------------------------------

class TestGetLoopbackPortBackwardsCompat:
    """Phase 2 tests call ``run_manager.get_loopback_port()`` expecting
    a string return. Phase 5 rewires it through waitress_config but
    must keep the str return type."""

    def test_returns_string(self, monkeypatch, tmp_path):
        monkeypatch.delenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", raising=False,
        )
        monkeypatch.delenv(
            "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL", raising=False,
        )
        monkeypatch.setattr(
            run_manager, "get_config_file_path",
            lambda _d: tmp_path / "missing.ini",
        )
        got = run_manager.get_loopback_port()
        assert isinstance(got, str)
        assert got == "8088"

    def test_env_override_still_works(self, monkeypatch):
        monkeypatch.setenv(
            "SETHLANS_MANAGER_LOOPBACK_PORT", "9001",
        )
        assert run_manager.get_loopback_port() == "9001"
