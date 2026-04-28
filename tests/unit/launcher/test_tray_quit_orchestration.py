# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #163 — top-level orchestration cleanup on tray quit.

Covers the cascade contracts:

* AC-WizardCleanup — ``run_wizard_mode`` on tray quit terminates
  wizard (and runtime if applicable), fires ``on_cold_boot_ready``,
  returns 0.
* AC-NormalModeCleanup — ``_await_cold_boot`` on tray quit fires
  ``on_cold_boot_ready`` and parallel-terminates manager + worker.
* AC-NoErrorCard — every quit path fires the success-path callback
  (``on_cold_boot_ready``), NOT the failure-path callback
  (``on_startup_failed``); browser MUST NOT open on quit.
* Hand-off: when ``wait_for_runtime_port_bind`` returns False due to
  tray quit (rather than a real port-bind timeout), the caller MUST
  NOT write a misleading ``.runtime_failed`` marker.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from launcher import (
    orchestration, supervision, wizard_orchestration, wizard_runtime,
)
from launcher.health_probe import QuitRequested

from ._tray_quit_helpers import (
    SECRET,
    FakeProc,
    args_ns,
    common_normal_mode_mocks,
    stage_port_file,
    write_topology,
)


@pytest.fixture(autouse=True)
def _mock_wizard_caddy(mocker):
    """Issue #170: stub the cert generator + Caddy supervisor."""
    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        return_value=(MagicMock(), MagicMock()),
    )
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        return_value=MagicMock(),
    )


# ----------------------------------------------------------------------
# AC-WizardCleanup — run_wizard_mode
# ----------------------------------------------------------------------

class TestRunWizardModeQuitCleanup:

    def test_quit_during_port_file_wait(self, tmp_path, mocker):
        mocker.patch(
            "launcher.wizard_orchestration._wait_for_wizard_port",
            return_value=None,
        )
        # Force the helper-distinguishing event check to True.
        mocker.patch.object(
            supervision.get_quit_requested_event(), "is_set",
            return_value=True,
        )
        ready = mocker.MagicMock()
        on_failed = mocker.MagicMock()
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=FakeProc()),
            on_cold_boot_ready=ready,
            on_startup_failed=on_failed,
            idle_timeout=2.0,
        )
        assert rc == 0
        ready.assert_called_once()
        on_failed.assert_not_called()  # AC-NoErrorCard
        terminate.assert_called_once()

    def test_quit_during_health_probe(self, tmp_path, mocker):
        stage_port_file(tmp_path)
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            side_effect=QuitRequested(),
        )
        ready = mocker.MagicMock()
        on_failed = mocker.MagicMock()
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=FakeProc()),
            on_cold_boot_ready=ready,
            on_startup_failed=on_failed,
            idle_timeout=2.0,
        )
        assert rc == 0
        ready.assert_called_once()
        on_failed.assert_not_called()
        terminate.assert_called_once()

    def test_quit_during_wizard_done_wait(self, tmp_path, mocker):
        stage_port_file(tmp_path)
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_orchestration.surface_wizard_url",
        )
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_wizard_done",
            return_value=(None, "quit_requested"),
        )
        ready = mocker.MagicMock()
        on_failed = mocker.MagicMock()
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=FakeProc()),
            on_cold_boot_ready=ready,
            on_startup_failed=on_failed,
            idle_timeout=2.0,
        )
        assert rc == 0
        # Cold-boot success path already fired ready once; quit
        # cleanup MUST NOT fire it again.
        assert ready.call_count == 1
        on_failed.assert_not_called()
        terminate.assert_called_once()


# ----------------------------------------------------------------------
# Runtime port-bind quit — caller-side disambiguation
# ----------------------------------------------------------------------

class TestHandOffToRuntimeQuit:

    def test_quit_during_port_bind_terminates_runtime(
        self, tmp_path, mocker,
    ):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager"}), encoding="utf-8",
        )
        runtime_proc = FakeProc()
        wizard_proc = FakeProc()
        mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=False,
        )
        write_marker = mocker.patch(
            "launcher.wizard_runtime.write_runtime_failed_marker",
        )
        terminate_runtime = mocker.patch(
            "launcher.wizard_runtime._terminate_runtime",
        )
        terminate_wizard = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        supervision.get_quit_requested_event().set()
        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=wizard_proc,
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=runtime_proc),
        )
        assert rc == 0
        # AC-NoErrorCard semantics: NO ``.runtime_failed`` marker.
        write_marker.assert_not_called()
        terminate_runtime.assert_called_once_with(runtime_proc)
        terminate_wizard.assert_called_once_with(wizard_proc)


# ----------------------------------------------------------------------
# AC-NormalModeCleanup — _await_cold_boot
# ----------------------------------------------------------------------

class TestNormalModeColdBootQuit:

    def test_quit_during_manager_probe(self, mocker, tmp_path):
        common_normal_mode_mocks(mocker)
        write_topology(tmp_path, "manager")
        mocker.patch.object(
            orchestration, "wait_for_health",
            side_effect=QuitRequested(),
        )
        opener = mocker.patch.object(orchestration, "open_browser")
        ready = mocker.MagicMock()
        on_failed = mocker.MagicMock()
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.wait.return_value = 0
        rc = orchestration.run_normal_mode(
            tmp_path, args_ns(), tray=None, secret="s",
            start_component=lambda *_a, **_k: manager_proc,
            on_cold_boot_ready=ready,
            on_startup_failed=on_failed,
        )
        assert rc == 0
        ready.assert_called_once()
        on_failed.assert_not_called()  # AC-NoErrorCard
        opener.assert_not_called()
        manager_proc.terminate.assert_called_once()

    def test_quit_during_worker_probe_terminates_both(
        self, mocker, tmp_path,
    ):
        common_normal_mode_mocks(mocker)
        write_topology(tmp_path, "manager_worker")
        results = iter([True, QuitRequested()])

        def _wait(*_a, **_k):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        mocker.patch.object(
            orchestration, "wait_for_health", side_effect=_wait,
        )
        mocker.patch.object(orchestration, "open_browser")
        ready = mocker.MagicMock()
        on_failed = mocker.MagicMock()
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.wait.return_value = 0
        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = None
        worker_proc.wait.return_value = 0

        def _start(name, **_k):
            return manager_proc if name == "manager" else worker_proc

        rc = orchestration.run_normal_mode(
            tmp_path, args_ns(), tray=None, secret="s",
            start_component=_start,
            on_cold_boot_ready=ready, on_startup_failed=on_failed,
        )
        assert rc == 0
        ready.assert_called_once()
        on_failed.assert_not_called()
        manager_proc.terminate.assert_called_once()
        worker_proc.terminate.assert_called_once()

    def test_quit_helper_returns_zero(self, mocker):
        ready = mocker.MagicMock()
        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.wait.return_value = 0
        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = None
        worker_proc.wait.return_value = 0
        rc = orchestration._quit_cold_boot(
            manager_proc, worker_proc, ready,
        )
        assert rc == 0
        ready.assert_called_once()
        manager_proc.terminate.assert_called_once()
        worker_proc.terminate.assert_called_once()
