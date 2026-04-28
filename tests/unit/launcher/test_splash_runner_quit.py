# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #162 tests for ``launcher.splash_runner``.

Covers FR-3 (orchestration completion quits Qt) and the idempotency
guarantee when both the Close-button path and ``_on_finished`` fire.
The splash runner mocks Qt fully so no event loop is spawned.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for splash_runner")

from launcher import splash_runner  # noqa: E402


class _StubSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _StubSplash:
    """Stub splash that stays "visible" until close() is called.

    Models the Tool-window behaviour: hiding the widget returns control
    to the runner, but only after _on_finished -> app.quit() has fired.
    """

    def __init__(self, *_args, **_kwargs):
        self._visible = True

    def show(self):
        self._visible = True

    def isVisible(self):
        return self._visible

    def close(self):
        self._visible = False

    def close_for_success(self):
        self.close()

    def morph_to_error(self, *_):
        # Failure path keeps the widget visible until the user clicks
        # Close (which would call .close() above).
        pass


class _ScriptedThread:
    """Thread stub the test drives manually via emit() calls."""

    def __init__(self, *_args, **_kwargs):
        self.cold_boot_ready = _StubSignal()
        self.startup_failed = _StubSignal()
        self.finished_with_code = _StubSignal()
        self.started = False

    def start(self):
        self.started = True

    def wait(self):
        pass


def _patch_runner(mocker, scripted_thread, stub_splash):
    """Patch splash_runner so we can drive _on_finished directly.

    Returns the QApplication mock so tests can assert on quit() calls.
    """
    qapp_cls = mocker.patch.object(splash_runner, "QApplication")
    qapp_inst = qapp_cls.instance.return_value = mocker.MagicMock()
    qapp_inst.applicationName.return_value = ""
    qapp_inst.applicationDisplayName.return_value = ""
    qapp_inst.organizationName.return_value = ""

    # exec() is a no-op — the test drives signals manually before
    # exec() is called, then exec() returns immediately.
    qapp_inst.exec.return_value = 0

    mocker.patch.object(
        splash_runner, "SethlansSplash", lambda *a, **kw: stub_splash,
    )
    mocker.patch.object(
        splash_runner, "OrchestrationThread",
        lambda *a, **kw: scripted_thread,
    )
    mocker.patch.object(splash_runner.supervision, "shutdown_supervisors")
    return qapp_inst


class TestOnFinishedQuitsQt:
    """FR-3 — _on_finished must call app.quit() so the first
    app.exec() returns once orchestration completes, regardless of
    whether the user has dismissed the splash."""

    def test_on_finished_calls_app_quit(self, mocker, tmp_path):
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)

        # Drive: thread emits startup_failed (rc=1), splash stays
        # visible, then finished_with_code(1) arrives. The runner's
        # _on_finished slot must call app.quit().
        def fake_exec():
            # First exec(): morph splash to error, then "finish".
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            return 0
        # Second exec() — error card waits for user; simulate user
        # clicking Close (which would call _dismiss_and_quit -> close
        # + app.quit). Splash has already been morphed; stub flips
        # visibility once we close it.
        call_count = {"n": 0}

        def driver():
            call_count["n"] += 1
            if call_count["n"] == 1:
                fake_exec()
            else:
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

        # _on_finished fired -> app.quit() called.
        qapp.quit.assert_called()
        # rc=1 was preserved (set by _on_failed).
        assert rc == 1


class TestIdempotentQuit:
    """NFR-1 — multiple app.quit() calls (from _on_finished AND from
    Close button) must not crash and must preserve the failure rc.
    """

    def test_app_quit_idempotent_when_close_and_finished_both_fire(
        self, mocker, tmp_path,
    ):
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        teardown_calls = []

        # Drive both quit paths in sequence:
        #   exec1: failure -> _on_failed (rc=1) -> _on_finished (quit
        #          via FR-3) -> exec1 returns
        #   exec2: simulate user Close click -> splash hidden + quit
        #          (FR-1) -> exec2 returns
        call_count = {"n": 0}

        def driver():
            call_count["n"] += 1
            if call_count["n"] == 1:
                thread.startup_failed.emit("boom", "tb")
                thread.finished_with_code.emit(1)
            else:
                # User clicks Close.
                splash.close()
                # Close button rewiring also triggers a second quit.
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
        # AC-TeardownReached: teardown_tray was reached after both
        # quit paths fired.
        assert teardown_calls == [None]
        # quit() was called multiple times (idempotent).
        assert qapp.quit.call_count >= 2
