# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for :mod:`launcher.orchestration_thread`.

Verifies signal wiring (post-v2 splash phase states refactor):

* ``cold_boot_ready`` fires when the wrapped orchestration callable
  invokes its ``on_cold_boot_ready`` hook.
* ``startup_failed(reason, traceback)`` fires when the wrapped
  callable raises before ``cold_boot_ready`` was emitted, OR when
  orchestration calls ``on_startup_failed`` directly (e.g. health
  timeout).
* ``finished_with_code`` fires at the end of a normal run with the
  orchestration's return code.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for QThread")
pytest.importorskip("pytestqt", reason="pytest-qt required for qtbot")

from launcher.orchestration_thread import (  # noqa: E402
    OrchestrationThread,
    _format_reason,
)


# ---- Signal emission --------------------------------------------------

class TestColdBootReadySignal:

    def test_emits_cold_boot_ready_when_hook_invoked(self, qtbot):
        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_startup_failed
            on_cold_boot_ready()
            return 0

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(thread.cold_boot_ready, timeout=2000):
            thread.start()
        thread.wait(2000)

    def test_emits_finished_with_code_on_clean_exit(self, qtbot):
        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_startup_failed
            on_cold_boot_ready()
            return 7

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(
            thread.finished_with_code, timeout=2000,
        ) as blocker:
            thread.start()
        thread.wait(2000)
        assert blocker.args == [7]

    def test_cold_boot_ready_only_fires_once(self, qtbot):
        count = {"n": 0}

        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_startup_failed
            on_cold_boot_ready()
            on_cold_boot_ready()  # second call should be a no-op
            return 0

        thread = OrchestrationThread(target)
        thread.cold_boot_ready.connect(
            lambda: count.update(n=count["n"] + 1),
        )
        with qtbot.waitSignal(thread.finished_with_code, timeout=2000):
            thread.start()
        thread.wait(2000)
        assert count["n"] == 1


# ---- startup_failed --------------------------------------------------

class TestStartupFailedSignal:

    def test_emits_startup_failed_when_target_raises(self, qtbot):
        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_cold_boot_ready, on_startup_failed
            raise RuntimeError("boom")

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(
            thread.startup_failed, timeout=2000,
        ) as blocker:
            thread.start()
        thread.wait(2000)
        reason, tb = blocker.args
        assert "boom" in reason
        assert "RuntimeError" in tb
        assert "boom" in tb

    def test_does_not_emit_startup_failed_if_ready_first(self, qtbot):
        """Post-ready exceptions must not revive the splash (FR-5)."""
        failed_calls = []

        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_startup_failed
            on_cold_boot_ready()
            raise RuntimeError("post-ready failure")

        thread = OrchestrationThread(target)
        thread.startup_failed.connect(
            lambda r, t: failed_calls.append((r, t)),
        )
        with qtbot.waitSignal(thread.finished_with_code, timeout=2000):
            thread.start()
        thread.wait(2000)
        assert failed_calls == []

    def test_explicit_on_startup_failed_callback_emits_signal(
        self, qtbot,
    ):
        """FR-11(c): orchestration may emit startup_failed on health timeout
        BEFORE returning, so the splash error card appears within ~250 ms."""
        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_cold_boot_ready
            on_startup_failed("worker did not start within 30 s", "")
            return 1

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(
            thread.startup_failed, timeout=2000,
        ) as blocker:
            thread.start()
        thread.wait(2000)
        reason, _trace = blocker.args
        assert "worker" in reason

    def test_explicit_on_startup_failed_then_finished_propagates_rc(
        self, qtbot,
    ):
        """The exit-code race fix (Option A): finished_with_code carries
        rc=1 when orchestration returns 1 after firing startup_failed."""
        def target(on_cold_boot_ready=None, on_startup_failed=None):
            del on_cold_boot_ready
            on_startup_failed("manager did not start within 30 s", "")
            return 1

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(
            thread.finished_with_code, timeout=2000,
        ) as blocker:
            thread.start()
        thread.wait(2000)
        assert blocker.args == [1]


# ---- _format_reason --------------------------------------------------

class TestFormatReason:

    def test_single_line_exception_returned_verbatim(self):
        exc = RuntimeError("port already in use")
        assert _format_reason(exc) == "port already in use"

    def test_multi_line_exception_wrapped_with_type(self):
        exc = RuntimeError("first line\nsecond line")
        reason = _format_reason(exc)
        assert reason.startswith("RuntimeError:")
        assert "first line" in reason
        assert "\n" not in reason

    def test_empty_exception_uses_type_name_only(self):
        exc = RuntimeError()
        assert _format_reason(exc) == "RuntimeError"
