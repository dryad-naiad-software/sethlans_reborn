# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cold-boot health + ordering tests for ``launcher.wizard_orchestration``.

Covers v2 splash phase states acceptance criteria for the wizard path:

* AC-D2 / FR-7 — Wizard mode dismisses splash on the wizard's own
  /api/health/ via ``wait_for_health(wizard_url, wizard_proc)``.
* AC-StartupFailedFirst / FR-11(c) — On wizard health timeout,
  ``startup_failed`` is emitted BEFORE ``terminate_wizard``.
* AC-D12 / FR-13 — Two independent budgets: 10 s port-file discovery
  THEN a separate 30 s health budget.
* AC-OpenBrowserOnSuccess / FR-12 — ``surface_wizard_url`` (which opens
  the browser) runs only after ``wait_for_health`` returns True.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest

from launcher import wizard_orchestration


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


@pytest.fixture(autouse=True)
def _mock_wizard_caddy(mocker):
    """Issue #170: stub the cert generator + Caddy supervisor."""
    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        return_value=(MagicMock(), MagicMock()),
    )
    fake_supervisor = MagicMock()
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        return_value=fake_supervisor,
    )


class _FakeProc:
    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_called = True
        if self.returncode is None:
            self.returncode = 0

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        del timeout
        return self.returncode or 0


def _stage_port_file(tmp_path, port=8101):
    (tmp_path / "wizard").mkdir()
    # Issue #170: wizard now writes its loopback port to
    # ``loopback_port``; the public-facing ``port`` file is owned by
    # the launcher (post-Caddy-up).
    (tmp_path / "wizard" / "loopback_port").write_text(str(port))


# ---------------------------------------------------------------------
# AC-D2 / FR-7 — wizard health success fires on_cold_boot_ready
# ---------------------------------------------------------------------

class TestColdBootReadyFires:

    def test_fires_after_health_succeeds_before_surface(
        self, tmp_path, mocker,
    ):
        """FR-7 / FR-12: on_cold_boot_ready fires AFTER wait_for_health
        returns True and BEFORE surface_wizard_url opens the browser."""
        _stage_port_file(tmp_path)
        order = []
        ready = MagicMock(side_effect=lambda: order.append("ready"))
        wait = mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            side_effect=lambda *_a, **_k: order.append("health") or True,
        )
        surface = mocker.patch(
            "launcher.wizard_orchestration.surface_wizard_url",
            side_effect=lambda *_a, **_k: order.append("surface"),
        )
        # Force idle_timeout=0 so wait_for_wizard_done returns idle_timeout
        # immediately and run_wizard_mode exits 1 before runtime spawn.
        wizard_proc = _FakeProc()
        mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        wizard_orchestration.run_wizard_mode(
            tmp_path, _args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=wizard_proc),
            on_cold_boot_ready=ready,
            idle_timeout=0.05,
        )
        # Ordering: health -> ready -> surface
        assert order.index("health") < order.index("ready")
        assert order.index("ready") < order.index("surface")
        wait.assert_called_once()
        surface.assert_called_once()


# ---------------------------------------------------------------------
# FR-11(c) / AC-StartupFailedFirst — wizard health timeout
# ---------------------------------------------------------------------

class TestWizardHealthTimeout:

    def test_health_timeout_emits_failed_before_terminate(
        self, tmp_path, mocker,
    ):
        _stage_port_file(tmp_path)
        order = []
        on_failed = MagicMock(
            side_effect=lambda *_a: order.append("failed"),
        )
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
            side_effect=lambda _p: order.append("terminate"),
        )
        # Health probe fails -> timeout path.
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=False,
        )
        # Browser should NOT be opened on failure.
        surface = mocker.patch(
            "launcher.wizard_orchestration.surface_wizard_url",
        )

        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, _args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=_FakeProc()),
            on_startup_failed=on_failed,
            on_cold_boot_ready=MagicMock(),
            idle_timeout=2.0,
        )
        assert rc == 1
        assert order.index("failed") < order.index("terminate")
        terminate.assert_called_once()
        surface.assert_not_called()

    def test_port_file_timeout_drives_splash_error_card(
        self, tmp_path, mocker,
    ):
        """FR-13 / AC-D12: port-file discovery failure also drives the
        splash to the error card (calls on_startup_failed)."""
        on_failed = MagicMock()
        # _wait_for_wizard_port returns None -> port-file timeout path.
        mocker.patch(
            "launcher.wizard_orchestration._wait_for_wizard_port",
            return_value=None,
        )
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, _args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=_FakeProc()),
            on_startup_failed=on_failed,
            on_cold_boot_ready=MagicMock(),
            idle_timeout=2.0,
        )
        assert rc == 1
        on_failed.assert_called_once()
        terminate.assert_called_once()

    def test_passes_wizard_proc_for_fast_fail(
        self, tmp_path, mocker,
    ):
        """FR-7 / AC-WizardCrashFastFail: wait_for_health gets the
        wizard_proc so a crash is detected within ~250 ms."""
        _stage_port_file(tmp_path)
        wizard_proc = _FakeProc()
        wait = mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        wizard_orchestration.run_wizard_mode(
            tmp_path, _args_ns(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=wizard_proc),
            on_cold_boot_ready=MagicMock(),
            idle_timeout=0.05,
        )
        # Verify the helper was invoked with wizard_proc as positional
        # argument 2 (FR-7).
        args = wait.call_args.args
        assert wizard_proc in args
