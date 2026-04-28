# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.diagnostics`` (issue #168).

Covers the five acceptance criteria for the diagnostics hooks:

* AC-MainExceptHook — uncaught main-thread exception is logged via
  ``logger.error`` with traceback.
* AC-ThreadExceptHook — uncaught background-thread exception is logged
  by the ``threading.excepthook`` install.
* AC-Idempotent — calling ``install_diagnostics`` twice is a no-op.
* AC-KIPassThrough — ``KeyboardInterrupt`` flows through to the default
  hook without being logged.
* AC-ExitSummary — ``log_exit_summary`` produces a single grep-friendly
  ``INFO`` line.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

import pytest

from launcher import diagnostics


# Each test isolates the module-level idempotence flag so installs in
# one test do not leak to another. The fixture also stashes/restores
# ``sys.excepthook`` and ``threading.excepthook`` because the hooks
# are process-global.
@pytest.fixture(autouse=True)
def _reset_diagnostics_state():
    saved_sys_hook = sys.excepthook
    saved_thread_hook = threading.excepthook
    diagnostics._reset_for_tests()
    try:
        yield
    finally:
        sys.excepthook = saved_sys_hook
        threading.excepthook = saved_thread_hook
        diagnostics._reset_for_tests()


# ---- AC-Idempotent ---------------------------------------------------------

class TestInstallIdempotent:
    def test_second_install_is_noop(self):
        diagnostics.install_diagnostics()
        first_hook = sys.excepthook
        first_thread_hook = threading.excepthook
        # Second call must not raise and must not replace the hooks.
        diagnostics.install_diagnostics()
        assert sys.excepthook is first_hook
        assert threading.excepthook is first_thread_hook


# ---- AC-MainExceptHook -----------------------------------------------------

class TestMainThreadExceptHook:
    def test_uncaught_exception_logged_with_traceback(self, caplog):
        diagnostics.install_diagnostics()
        try:
            raise RuntimeError("boom-from-main")
        except RuntimeError:
            exc_info = sys.exc_info()

        with caplog.at_level(logging.ERROR, logger="launcher.diagnostics"):
            # Patch ``sys.__excepthook__`` so the test does not print to
            # stderr / abort the test harness — the hook always defers
            # to the default after logging.
            saved = sys.__excepthook__
            sys.__excepthook__ = lambda *a, **kw: None
            try:
                sys.excepthook(*exc_info)
            finally:
                sys.__excepthook__ = saved

        records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
            and r.levelno == logging.ERROR
        ]
        assert len(records) == 1
        rec = records[0]
        assert "Uncaught main-thread exception" in rec.getMessage()
        assert "RuntimeError" in rec.getMessage()
        # Traceback attached via ``exc_info`` so the formatter can emit
        # the full stack — verify the record carries it.
        assert rec.exc_info is not None
        assert rec.exc_info[0] is RuntimeError


# ---- AC-ThreadExceptHook ---------------------------------------------------

class TestThreadingExceptHook:
    def test_thread_exception_logged(self, caplog):
        diagnostics.install_diagnostics()

        def _boom():
            raise ValueError("boom-from-thread")

        with caplog.at_level(logging.ERROR, logger="launcher.diagnostics"):
            t = threading.Thread(target=_boom, name="boom-worker")
            t.start()
            t.join(timeout=5.0)

        records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
            and r.levelno == logging.ERROR
        ]
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "Uncaught exception in thread" in msg
        assert "boom-worker" in msg
        assert "ValueError" in msg
        assert records[0].exc_info is not None
        assert records[0].exc_info[0] is ValueError


# ---- AC-KIPassThrough ------------------------------------------------------

class TestKeyboardInterruptPassThrough:
    def test_keyboard_interrupt_does_not_log_and_calls_default(self, caplog):
        diagnostics.install_diagnostics()

        called = {"count": 0}
        saved = sys.__excepthook__

        def _capture(exc_type, exc_value, exc_tb):
            called["count"] += 1
            assert exc_type is KeyboardInterrupt

        sys.__excepthook__ = _capture
        try:
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                exc_info = sys.exc_info()

            with caplog.at_level(
                logging.DEBUG, logger="launcher.diagnostics",
            ):
                sys.excepthook(*exc_info)
        finally:
            sys.__excepthook__ = saved

        assert called["count"] == 1
        # Must NOT have been logged at any level (NFR-5).
        records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
        ]
        assert records == []


# ---- AC-ExitSummary --------------------------------------------------------

class TestLogExitSummary:
    def test_format_contains_all_fields(self, caplog):
        started = time.monotonic() - 1.5  # 1.5s in the past
        with caplog.at_level(logging.INFO, logger="launcher.diagnostics"):
            diagnostics.log_exit_summary(
                rc=1, started_at=started,
                tray_torn_down=False, supervisors_shut_down=True,
            )

        records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
            and r.levelno == logging.INFO
        ]
        assert len(records) == 1
        msg = records[0].getMessage()
        # Grep-friendly format: ``Launcher exiting`` prefix + key=value
        # fields. Don't pin elapsed exactly (clock jitter) — pin the
        # other fields and that elapsed is present.
        assert msg.startswith("Launcher exiting ")
        assert "rc=1" in msg
        assert "elapsed=" in msg
        assert "tray_torn_down=False" in msg
        assert "supervisors_shut_down=True" in msg


# ---- finalize_main ---------------------------------------------------------

class TestFinalizeMain:
    """``diagnostics.finalize_main`` is the helper that ``run_launcher``
    delegates its ``finally`` block to. Verify each phase runs in
    isolation and the exit summary always lands."""

    def test_runs_all_phases_and_logs_summary(self, caplog):
        order = []

        def _shutdown():
            order.append("shutdown")

        def _release(lock):
            order.append(("release", lock))

        with caplog.at_level(logging.INFO, logger="launcher.diagnostics"):
            diagnostics.finalize_main(
                rc=0, started_at=time.monotonic(),
                shutdown_supervisors=_shutdown,
                release_lock=_release, instance_lock="LOCK",
            )

        assert order == ["shutdown", ("release", "LOCK")]
        assert any(
            "Launcher exiting" in r.getMessage()
            for r in caplog.records
            if r.name == "launcher.diagnostics"
        )

    def test_supervisor_failure_does_not_skip_release_or_summary(
        self, caplog,
    ):
        released = {"flag": False}

        def _bad_shutdown():
            raise RuntimeError("supervisor blew up")

        def _release(lock):
            released["flag"] = True

        with caplog.at_level(logging.DEBUG, logger="launcher.diagnostics"):
            diagnostics.finalize_main(
                rc=2, started_at=time.monotonic(),
                shutdown_supervisors=_bad_shutdown,
                release_lock=_release, instance_lock=None,
            )

        # Release ran despite shutdown failure.
        assert released["flag"] is True
        # Exit summary still landed, with supervisors_shut_down=False.
        info_records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
            and r.levelno == logging.INFO
        ]
        assert len(info_records) == 1
        assert "supervisors_shut_down=False" in info_records[0].getMessage()
        # Supervisor error was logged.
        err_records = [
            r for r in caplog.records
            if r.name == "launcher.diagnostics"
            and r.levelno == logging.ERROR
        ]
        assert any(
            "supervisor shutdown" in r.getMessage().lower()
            for r in err_records
        )
