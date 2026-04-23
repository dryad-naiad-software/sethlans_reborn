# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for :mod:`launcher.orchestration_thread`.

Verifies signal wiring:

* ``manager_ready`` fires when the wrapped orchestration callable
  invokes its ``on_manager_ready`` hook.
* ``startup_failed(reason, traceback)`` fires when the wrapped
  callable raises before ``manager_ready`` was emitted.
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

class TestManagerReadySignal:

    def test_emits_manager_ready_when_hook_invoked(self, qtbot):
        def target(on_manager_ready=None):
            on_manager_ready()
            return 0

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(thread.manager_ready, timeout=2000):
            thread.start()
        thread.wait(2000)

    def test_emits_finished_with_code_on_clean_exit(self, qtbot):
        def target(on_manager_ready=None):
            on_manager_ready()
            return 7

        thread = OrchestrationThread(target)
        with qtbot.waitSignal(
            thread.finished_with_code, timeout=2000,
        ) as blocker:
            thread.start()
        thread.wait(2000)
        assert blocker.args == [7]

    def test_manager_ready_only_fires_once(self, qtbot):
        count = {"n": 0}

        def target(on_manager_ready=None):
            on_manager_ready()
            on_manager_ready()  # second call should be a no-op
            return 0

        thread = OrchestrationThread(target)
        thread.manager_ready.connect(lambda: count.update(n=count["n"] + 1))
        with qtbot.waitSignal(thread.finished_with_code, timeout=2000):
            thread.start()
        thread.wait(2000)
        assert count["n"] == 1


# ---- startup_failed --------------------------------------------------

class TestStartupFailedSignal:

    def test_emits_startup_failed_when_target_raises(self, qtbot):
        def target(on_manager_ready=None):
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

        def target(on_manager_ready=None):
            on_manager_ready()
            raise RuntimeError("post-ready failure")

        thread = OrchestrationThread(target)
        thread.startup_failed.connect(
            lambda r, t: failed_calls.append((r, t)),
        )
        with qtbot.waitSignal(thread.finished_with_code, timeout=2000):
            thread.start()
        thread.wait(2000)
        assert failed_calls == []


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
