# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.main_dispatch._run_orchestration`` (issue #203).

Covers FR-LOOP1 / FR-LOOP2 / FR-LOOP5 fall-through behavior and the
three acceptance criteria around the wizard-to-normal-mode transition:

* AC-5: wizard rc!=0 -> ``run_normal_mode`` never called, rc propagates.
* AC-6: wizard rc=0 but ``.setup_complete`` missing -> returns 1.
* (positive) wizard rc=0 + sentinel present -> ``_bootstrap_first_run``
  is called, then ``run_normal_mode`` is called with the same args.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from launcher import main_dispatch


@pytest.fixture
def stubs(mocker, tmp_path):
    """Stub wizard mode, normal mode, and the injected callables."""
    run_wizard = mocker.patch.object(
        main_dispatch.wizard_orchestration, "run_wizard_mode",
        return_value=0,
    )
    run_normal = mocker.patch.object(
        main_dispatch.orchestration, "run_normal_mode",
        return_value=0,
    )
    bootstrap = MagicMock(return_value=tmp_path / "manager")
    start = MagicMock()
    return {
        "run_wizard": run_wizard,
        "run_normal": run_normal,
        "bootstrap": bootstrap,
        "start": start,
        "data_dir": tmp_path,
        "args": MagicMock(),
        "tray": MagicMock(),
        "secret": "ipc-secret",
    }


class TestRunOrchestrationWizardNonZero:
    """AC-5: wizard rc != 0 must propagate and short-circuit."""

    def test_returns_wizard_rc_without_calling_normal_mode(self, stubs):
        stubs["run_wizard"].return_value = 7
        rc = main_dispatch._run_orchestration(
            stubs["data_dir"], stubs["args"], stubs["tray"], stubs["secret"],
            bootstrap_first_run=stubs["bootstrap"],
            start_component=stubs["start"],
        )
        assert rc == 7
        stubs["run_wizard"].assert_called_once()
        stubs["run_normal"].assert_not_called()
        stubs["bootstrap"].assert_not_called()

    def test_wizard_rc_one_propagates(self, stubs):
        stubs["run_wizard"].return_value = 1
        rc = main_dispatch._run_orchestration(
            stubs["data_dir"], stubs["args"], stubs["tray"], stubs["secret"],
            bootstrap_first_run=stubs["bootstrap"],
            start_component=stubs["start"],
        )
        assert rc == 1
        stubs["run_normal"].assert_not_called()


class TestRunOrchestrationSentinelGuard:
    """AC-6: wizard rc=0 + sentinel missing -> rc=1, no normal-mode call."""

    def test_returns_one_when_setup_complete_missing(self, stubs):
        # Wizard "succeeds" but does not write .setup_complete.
        stubs["run_wizard"].return_value = 0
        # bootstrap_first_run is allowed to run (idempotent), but
        # _run_orchestration must NOT proceed to run_normal_mode.
        rc = main_dispatch._run_orchestration(
            stubs["data_dir"], stubs["args"], stubs["tray"], stubs["secret"],
            bootstrap_first_run=stubs["bootstrap"],
            start_component=stubs["start"],
        )
        assert rc == 1
        stubs["run_normal"].assert_not_called()
        # FR-LOOP5: bootstrap was attempted before the guard tripped.
        stubs["bootstrap"].assert_called_once_with(stubs["data_dir"])


class TestRunOrchestrationFallThrough:
    """Positive path: wizard rc=0 + sentinel present -> fall through."""

    def test_calls_bootstrap_then_normal_mode_with_same_args(self, stubs):
        # Wizard "writes" the sentinel via its side_effect (mirroring the
        # apply pipeline contract). Sentinel must NOT exist before the
        # wizard runs or _is_setup_complete short-circuits the branch.
        def _fake_wizard(data_dir, *_a, **_kw):
            (data_dir / ".setup_complete").touch()
            return 0
        stubs["run_wizard"].side_effect = _fake_wizard

        rc = main_dispatch._run_orchestration(
            stubs["data_dir"], stubs["args"], stubs["tray"], stubs["secret"],
            bootstrap_first_run=stubs["bootstrap"],
            start_component=stubs["start"],
            on_cold_boot_ready=MagicMock(),
            on_startup_failed=MagicMock(),
        )
        assert rc == 0
        # FR-LOOP5: bootstrap ran between wizard and normal mode.
        stubs["bootstrap"].assert_called_once_with(stubs["data_dir"])
        # AC-5 inverse: normal mode is called when wizard succeeds.
        stubs["run_normal"].assert_called_once()
        # FR-LOOP3: tray and secret reach run_normal_mode unchanged.
        args, _ = stubs["run_normal"].call_args
        assert args[0] is stubs["data_dir"]
        assert args[1] is stubs["args"]
        assert args[2] is stubs["tray"]
        assert args[3] is stubs["secret"]
        assert args[4] is stubs["start"]

    def test_skips_wizard_when_setup_already_complete(self, stubs):
        """Post-setup boot — no wizard call, straight to normal mode."""
        (stubs["data_dir"] / ".setup_complete").touch()

        rc = main_dispatch._run_orchestration(
            stubs["data_dir"], stubs["args"], stubs["tray"], stubs["secret"],
            bootstrap_first_run=stubs["bootstrap"],
            start_component=stubs["start"],
        )
        assert rc == 0
        stubs["run_wizard"].assert_not_called()
        # The post-setup branch does NOT call bootstrap (no need —
        # manager.ini already exists from the prior install).
        stubs["bootstrap"].assert_not_called()
        stubs["run_normal"].assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
