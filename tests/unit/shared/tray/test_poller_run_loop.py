# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``_run_loop`` top-level try/except survivability tests for
``QtStatePoller``.

Phase 8 Follow-up #1 wraps the ``_tick()`` call in a top-level
``try/except Exception`` so a synchronous raise does NOT kill the
polling thread silently.  These tests exercise that guard specifically
and were split out of ``test_poller_lifecycle.py`` to keep each file
under the 300-line cap (Phase 9).

Shared fixtures (``poller_factory_configurable``) come from
``conftest.py``.  ``_POLL_INTERVAL_SECONDS`` is patched to 0.05s so
the tests run in well under a second and are insensitive to CI
scheduling jitter.
"""

from __future__ import annotations

import logging
import threading
import time
import unittest.mock as _mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for poller")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from shared.tray import poller as poller_mod  # noqa: E402


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
            poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
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

        with _mock.patch.object(p, "_tick", side_effect=_flaky):
            with caplog.at_level(
                logging.ERROR, logger=poller_mod.logger.name,
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
            poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
        )
        p, stop, _flag, _ = poller_factory_configurable()
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
            poller_mod, "_POLL_INTERVAL_SECONDS", 0.05,
        )
        p, stop, _flag, _ = poller_factory_configurable()

        with _mock.patch.object(
            p, "_tick", side_effect=RuntimeError("nope"),
        ):
            with caplog.at_level(
                logging.ERROR, logger=poller_mod.logger.name,
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
