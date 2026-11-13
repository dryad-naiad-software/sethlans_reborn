# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``.setup_complete`` across the wizard hand-off (issue #210).

Worker-only skips the manager's apply pipeline, so
``hand_off_to_runtime`` records completion itself; it must do so only
after a successful hand-off, never on a failed or cancelled run, and
never on top of the sentinel ``apply_pending_setup`` writes for manager
topologies.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from launcher import main_dispatch, setup_sentinel, wizard_orchestration, wizard_runtime

from ._wizard_runtime_helpers import SECRET, FakeProc

SENTINEL = ".setup_complete"


def _write_topology(data_dir, topology):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topology}), encoding="utf-8",
    )


def _hand_off(data_dir, topology):
    return wizard_runtime.hand_off_to_runtime(
        payload={"topology": topology}, data_dir=data_dir,
        ipc_secret=SECRET, wizard_proc=FakeProc(),
    )


@pytest.fixture
def no_cleanup(mocker):
    return mocker.patch(
        "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
    )


class TestWorkerOnlyHandOff:

    def test_success_writes_sentinel(self, tmp_path, no_cleanup, mocker):
        _write_topology(tmp_path, "worker_only")
        pipeline = mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline",
        )
        assert _hand_off(tmp_path, "worker_only") == 0
        pipeline.assert_not_called()
        assert main_dispatch._is_setup_complete(tmp_path)
        data = json.loads((tmp_path / SENTINEL).read_text(encoding="utf-8"))
        assert data["topology"] == "worker_only"

    def test_sentinel_written_after_terminate_before_cleanup(
        self, tmp_path, mocker,
    ):
        _write_topology(tmp_path, "worker_only")
        order: list[str] = []
        mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
            side_effect=lambda *_a: order.append("terminate"),
        )
        real_record = setup_sentinel.record_setup_complete
        mocker.patch.object(
            wizard_runtime.setup_sentinel, "record_setup_complete",
            side_effect=lambda *a: order.append("sentinel") or real_record(*a),
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
            side_effect=lambda *_a: order.append("cleanup"),
        )
        assert _hand_off(tmp_path, "worker_only") == 0
        assert order == ["terminate", "sentinel", "cleanup"]

    @pytest.mark.parametrize(
        "error",
        [OSError("read-only fs"), ImportError("frozen build lacks module")],
    )
    def test_write_failure_fails_hand_off_and_keeps_wizard_dir(
        self, tmp_path, no_cleanup, mocker, error,
    ):
        _write_topology(tmp_path, "worker_only")
        mocker.patch.object(
            wizard_runtime.setup_sentinel, "record_setup_complete",
            side_effect=error,
        )
        assert _hand_off(tmp_path, "worker_only") == 1
        no_cleanup.assert_not_called()  # FR-L9: leave data dir intact
        assert not (tmp_path / SENTINEL).exists()

    def test_missing_topology_writes_nothing(self, tmp_path, no_cleanup):
        assert _hand_off(tmp_path, "worker_only") == 1
        assert not (tmp_path / SENTINEL).exists()

    @pytest.mark.parametrize("topology", ["universe", "WORKER_ONLY"])
    def test_unknown_topology_fails_with_its_own_reason(
        self, tmp_path, no_cleanup, mocker, topology,
    ):
        _write_topology(tmp_path, topology)
        pipeline = mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline",
        )
        failure = mocker.spy(wizard_runtime, "wizard_failure_exit")
        assert _hand_off(tmp_path, topology) == 1
        failure.assert_called_once_with("unknown_topology")
        pipeline.assert_not_called()
        no_cleanup.assert_not_called()
        assert not (tmp_path / SENTINEL).exists()


class TestManagerHandOff:

    @pytest.mark.parametrize("topology", ["manager", "manager_worker"])
    def test_apply_written_sentinel_is_not_overwritten(
        self, tmp_path, no_cleanup, mocker, topology,
    ):
        _write_topology(tmp_path, topology)
        marker = b'{"written_by": "apply_pending_setup"}'

        def _fake_pipeline(data_dir):
            (data_dir / SENTINEL).write_bytes(marker)
            return True, ""
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline",
            side_effect=_fake_pipeline,
        )
        assert _hand_off(tmp_path, topology) == 0
        assert (tmp_path / SENTINEL).read_bytes() == marker

    def test_apply_failure_writes_nothing(self, tmp_path, no_cleanup, mocker):
        _write_topology(tmp_path, "manager")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline",
            return_value=(False, "migrate failed"),
        )
        assert _hand_off(tmp_path, "manager") == 1
        assert not (tmp_path / SENTINEL).exists()

    def test_apply_success_without_sentinel_is_not_papered_over(
        self, tmp_path, no_cleanup, mocker,
    ):
        """If apply claims success but wrote nothing, the launcher must
        not invent the sentinel; the main_dispatch guard has to trip."""
        _write_topology(tmp_path, "manager_worker")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline",
            return_value=(True, ""),
        )
        assert _hand_off(tmp_path, "manager_worker") == 0
        assert not (tmp_path / SENTINEL).exists()


class TestNonDoneReasonsNeverWrite:
    """Cancelled / failed wizard runs never reach the hand-off."""

    @pytest.mark.parametrize(
        "reason", ["quit_requested", "idle_timeout", "wizard_exited"],
    )
    def test_no_sentinel(self, tmp_path, mocker, reason):
        _write_topology(tmp_path, "worker_only")
        mocker.patch.object(wizard_orchestration, "_stop_wizard_caddy")
        mocker.patch.object(wizard_runtime, "terminate_wizard")
        hand_off = mocker.patch.object(wizard_runtime, "hand_off_to_runtime")
        wizard_orchestration._finish_after_done(
            None, reason, FakeProc(), None, tmp_path, SECRET,
            MagicMock(), MagicMock(),
        )
        hand_off.assert_not_called()
        assert not (tmp_path / SENTINEL).exists()


class TestRunOrchestrationWorkerOnly:
    """Regression for the #203 guard: worker-only falls through."""

    def test_first_run_falls_through_then_later_launch_skips_wizard(
        self, tmp_path, no_cleanup, mocker,
    ):
        def _fake_wizard(data_dir, *_a, **_kw):
            _write_topology(data_dir, "worker_only")
            return _hand_off(data_dir, "worker_only")
        run_wizard = mocker.patch.object(
            main_dispatch.wizard_orchestration, "run_wizard_mode",
            side_effect=_fake_wizard,
        )
        run_normal = mocker.patch.object(
            main_dispatch.orchestration, "run_normal_mode", return_value=0,
        )

        def _run():
            return main_dispatch._run_orchestration(
                tmp_path, MagicMock(), None, "s",
                bootstrap_first_run=MagicMock(),
                start_component=MagicMock(),
            )

        assert _run() == 0
        assert run_normal.call_count == 1
        assert _run() == 0
        assert run_wizard.call_count == 1  # second launch skipped it
        assert run_normal.call_count == 2
