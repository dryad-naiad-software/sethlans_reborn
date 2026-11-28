# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.splash_runner`` quit semantics.

Issue #162 originally added FR-3 (orchestration completion quits Qt).
Issue #164 reverted that behaviour: on the failure side, the only
legitimate quit paths are user-driven (Close button via
``_dismiss_and_quit``; alt-F4 / OS close via the ``closeEvent`` override
on ``SethlansSplash``).  These tests pin both the "finished alone does
not quit" guarantee and the idempotency / rc-preservation guarantee
when both ``_on_finished`` and the user-dismissal path fire. The quit
timer (issue #163) is in ``test_splash_runner_quit_timer.py``.

The splash runner mocks Qt fully (``_splash_runner_stubs``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for splash_runner")

from launcher import splash_runner  # noqa: E402
from tests.unit.launcher._splash_runner_stubs import (  # noqa: E402
    _patch_runner, _ScriptedThread, _StubSplash,
)


class TestOnFinishedDoesNotQuitQt:
    """Issue #164 — _on_finished must NOT call app.quit() on the
    failure side.  The error card stays visible indefinitely until the
    user explicitly dismisses it (Close button, alt-F4, or OS close).
    """

    def test_finished_alone_does_not_quit_app(self, mocker, tmp_path):
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)

        # Drive: thread emits startup_failed (rc=1), splash stays
        # visible, then finished_with_code(1) arrives. The runner's
        # _on_finished slot must NOT call app.quit() — only the user
        # dismissing the splash is allowed to do that.
        call_count = {"n": 0}

        def driver():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First exec(): orchestration fails and finishes; the
                # runner used to quit here, but no longer does. To
                # keep the test fast, simulate the user dismissing the
                # splash so the first exec() can return.
                thread.startup_failed.emit("boom", "tb")
                thread.finished_with_code.emit(1)
                # Only signals fired; no quit yet — assert that here
                # before we manually dismiss.
                assert qapp.quit.call_count == 0
                splash.close()
            return 0
        qapp.exec.side_effect = driver

        rc = splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda _t: None,
        )

        # rc=1 was preserved (set by _on_failed).
        assert rc == 1


class TestSplashStaysVisible:
    """Issue #164 — after orchestration finishes with failure, the
    splash must stay visible until the user dismisses it.  Only the
    user-driven Close path is allowed to call ``app.quit()``.
    """

    def test_splash_stays_visible_after_orchestration_finishes(
        self, mocker, tmp_path,
    ):
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)

        observations = {"after_finished_visible": None,
                        "after_finished_quit_calls": None}

        def driver():
            # Failure -> finished, then observe state, then user Close.
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            observations["after_finished_visible"] = splash.isVisible()
            observations["after_finished_quit_calls"] = qapp.quit.call_count
            # Now simulate the user clicking Close — that path closes
            # the splash and calls app.quit() (driven by the real
            # SethlansSplash._dismiss_and_quit; we model it explicitly).
            splash.close()
            qapp.quit()
            return 0
        qapp.exec.side_effect = driver

        rc = splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda _t: None,
        )

        # After orchestration finished, splash was still visible AND
        # app.quit() had not yet been called.
        assert observations["after_finished_visible"] is True
        assert observations["after_finished_quit_calls"] == 0
        # Then the user-driven Close path quits Qt; rc preserved.
        assert qapp.quit.call_count == 1
        assert rc == 1


class TestIdempotentQuit:
    """NFR-1 — multiple quit paths in sequence must not crash and must
    preserve the failure rc.  After #164, the only quit source on the
    failure side is the user; this test pins that behaviour and that
    teardown is reached.
    """

    def test_user_dismiss_after_finished_preserves_rc_and_reaches_teardown(
        self, mocker, tmp_path,
    ):
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        teardown_calls = []

        # Drive the failure path:
        #   exec1: startup_failed (rc=1) + finished_with_code(1) ->
        #          splash stays visible -> user clicks Close -> quit.
        def driver():
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            # Splash still visible — user dismisses.
            splash.close()
            qapp.quit()
            return 0
        qapp.exec.side_effect = driver

        rc = splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda t: teardown_calls.append(t),
        )

        # No exception, rc preserved at 1.
        assert rc == 1
        # AC-TeardownReached: teardown_tray was reached after the user
        # dismissed the splash.
        assert teardown_calls == [None]
        # quit() was called exactly once (by the user-Close path).
        assert qapp.quit.call_count == 1
