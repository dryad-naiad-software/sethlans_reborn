# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Thread-lifecycle + cross-thread delivery tests for ``QtStatePoller``.

Split from ``test_qt_poller.py`` to keep each file under the 300-line
cap.  Covers:

* ``start()`` thread spawning + idempotency.
* ``stop_event`` + ``join(timeout)`` clean shutdown.
* ``launcher_gone`` one-shot guard.
* Cross-thread signal delivery (slot invoked on GUI thread even though
  the signal is emitted from the poller thread).
* Non-coalescing signal delivery per FR-6.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_poller")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtCore import Qt, QObject  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402

from shared.tray import qt_poller as qt_poller_mod  # noqa: E402
from shared.tray.qt_poller import ManagerSnapshot  # noqa: E402


def _poller_threads_named(name: str):
    return [t for t in threading.enumerate() if t.name == name]


# ------------------------------------------------------------------
# Thread lifecycle
# ------------------------------------------------------------------

class TestThreadLifecycle:

    def test_start_spawns_named_daemon_thread(
        self, poller_factory_configurable,
    ):
        p, stop, _flag, _ = poller_factory_configurable()
        p.start()
        try:
            assert p._thread is not None
            assert p._thread.is_alive()
            assert p._thread.daemon is True
            assert p._thread.name == "tray-state-poller"
        finally:
            stop.set()
            p.join(timeout=3.0)
        assert not p._thread.is_alive()

    def test_start_is_idempotent(
        self, poller_factory_configurable, caplog,
    ):
        p, stop, _flag, _ = poller_factory_configurable()
        p.start()
        first_thread = p._thread
        with caplog.at_level(
            logging.WARNING, logger=qt_poller_mod.logger.name,
        ):
            p.start()
        try:
            assert p._thread is first_thread
            named = _poller_threads_named("tray-state-poller")
            assert len(named) == 1
            assert any(
                "already running" in rec.message
                for rec in caplog.records
            )
        finally:
            stop.set()
            p.join(timeout=3.0)

    def test_start_after_clean_exit_spawns_new_thread(
        self, poller_factory_configurable,
    ):
        p, stop, _flag, _ = poller_factory_configurable()
        p.start()
        stop.set()
        p.join(timeout=3.0)
        assert not p._thread.is_alive()

        stop.clear()
        old_thread = p._thread
        p.start()
        try:
            assert p._thread is not old_thread
            assert p._thread.is_alive()
        finally:
            stop.set()
            p.join(timeout=3.0)

    def test_stop_event_terminates_thread_within_timeout(
        self, poller_factory_configurable,
    ):
        p, stop, _flag, _ = poller_factory_configurable()
        p.start()
        assert p._thread.is_alive()
        stop.set()
        start_time = time.monotonic()
        p.join(timeout=3.0)
        elapsed = time.monotonic() - start_time
        assert elapsed < 3.0
        assert not p._thread.is_alive()

    def test_join_without_start_is_noop(self, poller_factory_configurable):
        p, _stop, _flag, _ = poller_factory_configurable()
        p.join(timeout=0.1)


# ------------------------------------------------------------------
# Launcher-gone one-shot
# ------------------------------------------------------------------

class TestLauncherGoneOneShot:

    def test_emits_once_and_sets_stop_event(
        self, poller_factory_configurable,
    ):
        p, stop, _flag, _alive = poller_factory_configurable(
            launcher_alive=False,
        )
        spy = QSignalSpy(p.launcher_gone)
        p._tick()
        assert spy.count() == 1
        assert stop.is_set()

    def test_second_tick_does_not_reemit(
        self, poller_factory_configurable,
    ):
        p, stop, _flag, _alive = poller_factory_configurable(
            launcher_alive=False,
        )
        spy = QSignalSpy(p.launcher_gone)
        p._tick()
        stop.clear()
        p._tick()
        assert spy.count() == 1


# ------------------------------------------------------------------
# Cross-thread delivery (headline test)
# ------------------------------------------------------------------

class TestSignalCrossThread:

    def test_slot_invoked_on_gui_thread(
        self, qapp, qtbot, poller_factory_configurable,
    ):
        payload = {"boot_id": "b1", "version": "v"}
        p, stop, _flag, _ = poller_factory_configurable(
            script=[payload] * 100,
        )

        gui_thread = threading.current_thread()
        received = []

        def _slot(snap):
            received.append((threading.current_thread(), snap))

        # QueuedConnection forces marshalling through the event loop.
        p.snapshot_changed.connect(
            _slot, type=Qt.ConnectionType.QueuedConnection,
        )

        with qtbot.waitSignal(p.snapshot_changed, timeout=5000):
            p.start()

        # Pump events so the queued slot fires.
        qapp.processEvents()
        deadline = time.monotonic() + 2.0
        while not received and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)

        stop.set()
        p.join(timeout=3.0)

        assert received, "slot never fired on GUI thread"
        slot_thread, snap = received[0]
        assert slot_thread is gui_thread
        assert isinstance(snap, ManagerSnapshot)


# ------------------------------------------------------------------
# Shutdown sequence (stop_event -> join -> disconnect)
# ------------------------------------------------------------------

class TestShutdownSequence:

    def test_disconnect_after_join_does_not_raise(
        self, poller_factory_configurable,
    ):
        payload = {"boot_id": "b1"}
        p, stop, _flag, _ = poller_factory_configurable(
            script=[payload] * 50,
        )
        p.start()
        stop.set()
        p.join(timeout=3.0)
        # PySide6 exposes the ``disconnect-all-slots`` shutdown via the
        # static ``QObject.disconnect(sender)`` form — there is no
        # instance-level ``p.disconnect()`` with zero args.  Must not
        # raise.
        QObject.disconnect(p)

    def test_no_signals_after_join(
        self, qapp, poller_factory_configurable,
    ):
        payload = {"boot_id": "b1"}
        p, stop, _flag, _ = poller_factory_configurable(
            script=[payload] * 100,
        )
        spy = QSignalSpy(p.snapshot_changed)
        p.start()
        stop.set()
        p.join(timeout=3.0)
        qapp.processEvents()

        count_after_join = spy.count()
        time.sleep(0.2)
        qapp.processEvents()
        assert spy.count() == count_after_join


# ------------------------------------------------------------------
# Non-coalescing signal delivery (FR-6)
# ------------------------------------------------------------------

class TestNoCoalescing:

    def test_five_distinct_ticks_produce_five_emits(
        self, poller_factory_configurable,
    ):
        script = [
            {"boot_id": "b1", "workers_online": i, "setup_mode": True}
            for i in range(5)
        ]
        p, _stop, _flag, _ = poller_factory_configurable(script=script)
        spy = QSignalSpy(p.snapshot_changed)

        for _ in range(5):
            p._tick()

        assert spy.count() == 5
        values = [spy.at(i)[0].workers_online for i in range(spy.count())]
        assert values == [0, 1, 2, 3, 4]


# ------------------------------------------------------------------
# _run_loop top-level try/except (Follow-up #1)
# ------------------------------------------------------------------

class TestRunLoopTryExcept:
    """Phase 8 Follow-up #1: ``_run_loop`` wraps the ``_tick()`` call
    in a top-level ``try/except Exception`` so a synchronous raise
    does NOT kill the polling thread silently."""

    def test_tick_exception_does_not_kill_thread(
        self, poller_factory_configurable, caplog, mocker,
    ):
        # Lower the inter-tick wait so two ticks complete in well under
        # a second and the test isn't sensitive to CI scheduling jitter.
        mocker.patch.object(
            qt_poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
        )
        p, stop, _flag, _ = poller_factory_configurable()
        # Replace _tick to raise once, succeed after.
        call_count = {"n": 0}
        tick_event = threading.Event()

        def _flaky():
            call_count["n"] += 1
            tick_event.set()
            if call_count["n"] == 1:
                raise RuntimeError("boom")

        import unittest.mock as _mock
        with _mock.patch.object(p, "_tick", side_effect=_flaky):
            import logging
            with caplog.at_level(
                logging.ERROR, logger=qt_poller_mod.logger.name,
            ):
                p.start()
                # Wait for at least two ticks (first raises, second is
                # the "still alive" proof).  Interval patched to 0.05s
                # above; 2s deadline is ample headroom for slow CI.
                deadline = time.monotonic() + 2.0
                while (call_count["n"] < 2
                       and time.monotonic() < deadline):
                    time.sleep(0.01)

                assert call_count["n"] >= 2, (
                    "Second tick never ran — thread died after first "
                    "exception instead of continuing"
                )
                assert p._thread.is_alive(), (
                    "Thread died after a _tick() exception"
                )
                # Exception must have been logged (not swallowed).
                assert any(
                    "tick" in rec.getMessage().lower() or rec.exc_info
                    for rec in caplog.records
                )
            stop.set()
            p.join(timeout=3.0)

    def test_loop_terminates_on_stop_event_after_failing_tick(
        self, poller_factory_configurable, mocker,
    ):
        mocker.patch.object(
            qt_poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
        )
        p, stop, _flag, _ = poller_factory_configurable()
        import unittest.mock as _mock
        with _mock.patch.object(
            p, "_tick", side_effect=RuntimeError("always"),
        ):
            p.start()
            # Let the loop spin at least once.
            time.sleep(0.1)
            stop.set()
            # Thread must exit within the poll interval + a small
            # cushion (loop.wait(0.05) returns when stop is set).
            start_time = time.monotonic()
            p.join(timeout=2.0)
            elapsed = time.monotonic() - start_time
            assert not p._thread.is_alive(), (
                f"Thread still alive after stop set "
                f"(elapsed={elapsed:.2f}s)"
            )

    def test_logger_exception_called_on_tick_failure(
        self, poller_factory_configurable, caplog, mocker,
    ):
        mocker.patch.object(
            qt_poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
        )
        p, stop, _flag, _ = poller_factory_configurable()
        import logging
        import unittest.mock as _mock

        with _mock.patch.object(
            p, "_tick", side_effect=RuntimeError("nope"),
        ):
            with caplog.at_level(
                logging.ERROR, logger=qt_poller_mod.logger.name,
            ):
                p.start()
                # Wait up to 2s for at least one ERROR record with
                # exc_info (i.e., logger.exception was called).
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if any(
                        rec.exc_info and
                        rec.levelno == logging.ERROR
                        for rec in caplog.records
                    ):
                        break
                    time.sleep(0.05)
            stop.set()
            p.join(timeout=3.0)

        err_records = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and r.exc_info
        ]
        assert err_records, (
            "Expected at least one logger.exception(...) call from "
            "_run_loop's try/except"
        )
