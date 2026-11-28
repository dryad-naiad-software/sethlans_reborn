# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The splash path waits for the loop thread in short slices (concurrency review of 6b, #1)."""

from __future__ import annotations

from launcher import splash_runner
from tests.unit.launcher._splash_runner_stubs import _patch_runner, _ScriptedThread, _StubSplash


class TestBoundedThreadWait:
    """Concurrency review of 6b (#1): one long ``thread.wait()`` kept Python signal handlers
    from ever running on the main thread for the whole of RUNNING."""

    def test_the_loop_thread_is_waited_for_in_short_slices(self, mocker, tmp_path):
        """SIGTERM/SIGINT reach the launcher while the splash path waits for the loop."""
        thread = _ScriptedThread()
        answers = iter([False, False, True])
        slices = []

        def wait(ms=None):
            slices.append(ms)
            return next(answers)
        thread.wait = wait
        _patch_runner(mocker, thread, _StubSplash())

        splash_runner.run_with_splash(
            args=mocker.MagicMock(), data_dir=tmp_path, version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 0, teardown_tray=lambda _t: None,
        )

        assert slices == [splash_runner.THREAD_WAIT_SLICE_MS] * 3
