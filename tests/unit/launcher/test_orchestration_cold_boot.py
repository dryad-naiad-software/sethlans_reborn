# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cold-boot health + termination ordering tests for ``launcher.orchestration``.

Covers v2 splash phase states acceptance criteria:

* AC-D2 — Splash dismisses on the first healthy /api/health/ from the
  cold-boot child for all four paths.
* AC-Serial / FR-6 — Manager+worker mode polls strictly serially under
  a single shared 30 s wall-clock deadline.
* AC-StartupFailedFirst / FR-11(c) — startup_failed is emitted BEFORE
  proc.terminate() on health timeout.
* AC-ParallelTerminate / FR-11(b) — both terminate() calls happen
  before either wait().
* AC-OpenBrowserOnSuccess / FR-12 — open_browser runs only after
  on_cold_boot_ready fires; on timeout the browser is NOT opened.
* AC-RunSetupModeDeleted / FR-9 — ``run_setup_mode`` is gone.
"""

from __future__ import annotations

import argparse
import json

import pytest

from launcher import cold_boot, orchestration


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def _write_topology(data_dir, topo):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topo}), encoding="utf-8",
    )


def _common_normal_mode_mocks(mocker):
    mocker.patch.object(orchestration, "remove_setup_section")
    mocker.patch.object(orchestration, "start_caddy_supervisor")
    # Short-circuit the IPC poll loop on success path.
    mocker.patch.object(
        orchestration, "_all_live_exited", return_value=True,
    )


# ---------------------------------------------------------------------
# AC-RunSetupModeDeleted (FR-9)
# ---------------------------------------------------------------------

class TestRunSetupModeDeleted:

    def test_run_setup_mode_is_not_importable(self):
        """FR-9 / AC-RunSetupModeDeleted: ``run_setup_mode`` must not be
        a public attribute of ``launcher.orchestration``."""
        assert not hasattr(orchestration, "run_setup_mode")


# ---------------------------------------------------------------------
# AC-D2 — single-URL paths fire on_cold_boot_ready exactly once
# ---------------------------------------------------------------------

class TestColdBootReadyFiresOncePerPath:

    def test_manager_only_topology(self, mocker, tmp_path):
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager")
        wait = mocker.patch.object(
            orchestration, "wait_for_health", return_value=True,
        )
        opener = mocker.patch.object(orchestration, "open_browser")
        ready = mocker.MagicMock()
        manager_proc = mocker.MagicMock()

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=lambda *_a, **_k: manager_proc,
            on_cold_boot_ready=ready,
        )
        assert rc == 0
        ready.assert_called_once()
        assert wait.call_count == 1
        opener.assert_called_once()

    def test_worker_only_topology(self, mocker, tmp_path):
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "worker")
        wait = mocker.patch.object(
            orchestration, "wait_for_health", return_value=True,
        )
        opener = mocker.patch.object(orchestration, "open_browser")
        ready = mocker.MagicMock()
        worker_proc = mocker.MagicMock()

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=lambda *_a, **_k: worker_proc,
            on_cold_boot_ready=ready,
        )
        assert rc == 0
        ready.assert_called_once()
        assert wait.call_count == 1
        # Worker-only never opens the browser.
        opener.assert_not_called()


# ---------------------------------------------------------------------
# AC-Serial (FR-6) — manager+worker shared 30 s deadline
# ---------------------------------------------------------------------

class TestManagerWorkerSerial:

    def test_polls_manager_first_then_worker(self, mocker, tmp_path):
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager_worker")
        opener = mocker.patch.object(orchestration, "open_browser")
        seen_urls = []

        def _fake_wait(url, _proc, timeout=30.0):
            seen_urls.append(url)
            return True

        mocker.patch.object(
            orchestration, "wait_for_health", side_effect=_fake_wait,
        )
        ready = mocker.MagicMock()
        manager_proc = mocker.MagicMock()
        worker_proc = mocker.MagicMock()

        def _start(name, **_k):
            return manager_proc if name == "manager" else worker_proc

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=_start, on_cold_boot_ready=ready,
        )
        assert rc == 0
        assert len(seen_urls) == 2
        assert "127.0.0.1:8080" in seen_urls[0]
        assert "127.0.0.1:8081" in seen_urls[1]
        ready.assert_called_once()
        opener.assert_called_once()

    def test_shared_deadline_arithmetic_passes_remaining_to_worker(
        self, mocker, tmp_path,
    ):
        """FR-6 / AC-Serial: when manager succeeds at t=29 s of the
        30 s shared deadline, the worker call gets timeout ≈ 1 s."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager_worker")
        mocker.patch.object(orchestration, "open_browser")

        # Drive monotonic so manager "took" 29 s.
        # Sequence: deadline = monotonic() + 30, then check before manager
        # call (returns ~0), then check before worker call (returns 29).
        monotonic_values = iter([0.0, 0.0, 29.0])
        mocker.patch.object(
            orchestration.time, "monotonic",
            side_effect=lambda: next(monotonic_values),
        )

        timeouts_seen = []

        def _fake_wait(_url, _proc, timeout=30.0):
            timeouts_seen.append(timeout)
            return True

        mocker.patch.object(
            orchestration, "wait_for_health", side_effect=_fake_wait,
        )

        manager_proc = mocker.MagicMock()
        worker_proc = mocker.MagicMock()

        def _start(name, **_k):
            return manager_proc if name == "manager" else worker_proc

        orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=_start,
            on_cold_boot_ready=mocker.MagicMock(),
        )
        assert len(timeouts_seen) == 2
        # Manager call gets the full 30 s.
        assert timeouts_seen[0] == pytest.approx(30.0, abs=0.01)
        # Worker call gets ~1 s remaining (30 - 29 = 1).
        assert timeouts_seen[1] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------
# AC-StartupFailedFirst / AC-ParallelTerminate / FR-11
# ---------------------------------------------------------------------

class TestHealthTimeoutOrdering:

    def test_startup_failed_emitted_before_terminate(
        self, mocker, tmp_path,
    ):
        """FR-11(c) / AC-StartupFailedFirst: on health timeout,
        ``startup_failed`` MUST be emitted BEFORE ``proc.terminate()``."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager")
        # wait_for_health returns False immediately -> timeout path.
        mocker.patch.object(
            orchestration, "wait_for_health", return_value=False,
        )
        opener = mocker.patch.object(orchestration, "open_browser")

        order = []
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.terminate.side_effect = (
            lambda: order.append("terminate")
        )
        # wait() in cold_boot helper returns immediately.
        manager_proc.wait.return_value = 0

        def _on_failed(*_args):
            order.append("startup_failed")

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=lambda *_a, **_k: manager_proc,
            on_cold_boot_ready=mocker.MagicMock(),
            on_startup_failed=_on_failed,
        )
        assert rc == 1
        assert "startup_failed" in order
        assert "terminate" in order
        assert order.index("startup_failed") < order.index("terminate")
        # AC-OpenBrowserOnSuccess: browser NOT opened on timeout.
        opener.assert_not_called()

    def test_parallel_terminate_both_before_either_wait(
        self, mocker, tmp_path,
    ):
        """FR-11(b) / AC-ParallelTerminate: in manager+worker mode,
        both terminate() calls must happen BEFORE either wait()."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager_worker")

        # Manager succeeds, worker times out.
        results = iter([True, False])
        mocker.patch.object(
            orchestration, "wait_for_health",
            side_effect=lambda *_a, **_k: next(results),
        )
        mocker.patch.object(orchestration, "open_browser")

        order = []
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.terminate.side_effect = (
            lambda: order.append("manager.terminate")
        )
        manager_proc.wait.side_effect = (
            lambda timeout=None: order.append("manager.wait") or 0
        )
        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = None
        worker_proc.terminate.side_effect = (
            lambda: order.append("worker.terminate")
        )
        worker_proc.wait.side_effect = (
            lambda timeout=None: order.append("worker.wait") or 0
        )

        def _start(name, **_k):
            return manager_proc if name == "manager" else worker_proc

        orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=_start,
            on_cold_boot_ready=mocker.MagicMock(),
            on_startup_failed=mocker.MagicMock(),
        )

        # Both terminate() calls happen BEFORE either wait().
        first_wait = min(
            order.index("manager.wait"), order.index("worker.wait"),
        )
        last_terminate = max(
            order.index("manager.terminate"),
            order.index("worker.terminate"),
        )
        assert last_terminate < first_wait, order

    def test_browser_not_opened_on_health_timeout(
        self, mocker, tmp_path,
    ):
        """AC-OpenBrowserOnSuccess (FR-12)."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager_worker")
        mocker.patch.object(
            orchestration, "wait_for_health", return_value=False,
        )
        opener = mocker.patch.object(orchestration, "open_browser")
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = None

        def _start(name, **_k):
            return manager_proc if name == "manager" else worker_proc

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=_start,
            on_cold_boot_ready=mocker.MagicMock(),
            on_startup_failed=mocker.MagicMock(),
        )
        assert rc == 1
        opener.assert_not_called()


# ---------------------------------------------------------------------
# cold_boot.parallel_terminate / fail_cold_boot direct unit tests
# ---------------------------------------------------------------------

class TestColdBootHelpers:

    def test_fail_cold_boot_calls_failed_then_terminate(self, mocker):
        order = []
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.terminate.side_effect = (
            lambda: order.append("terminate")
        )
        manager_proc.wait.return_value = 0
        on_failed = mocker.MagicMock(
            side_effect=lambda *_a: order.append("failed"),
        )

        rc = cold_boot.fail_cold_boot(
            "boom", manager_proc, None, on_failed,
        )
        assert rc == 1
        assert order.index("failed") < order.index("terminate")

    def test_safe_invoke_swallows_callback_exceptions(self, mocker):
        bad = mocker.MagicMock(side_effect=RuntimeError("splat"))
        # Must not raise.
        cold_boot.safe_invoke(bad, "arg")
        bad.assert_called_once_with("arg")

    def test_safe_invoke_handles_none(self):
        # Must not raise.
        cold_boot.safe_invoke(None)
