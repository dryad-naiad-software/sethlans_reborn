# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_runtime`` — topology read / handoff /
termination facets.

Issue #203 stripped the runtime-spawn + port-bind facets from
``hand_off_to_runtime``; the per-helper port-bind tests in the old
``test_wizard_runtime_health.py`` were deleted with their target code.
What remains here is the topology-read, ``spawn_runtime_for_topology``
helper (retained for direct callers), ``terminate_wizard``, and the
slimmed-down ``hand_off_to_runtime``.
"""

import json
from unittest.mock import MagicMock

import pytest

from launcher import wizard_runtime

from ._wizard_runtime_helpers import SECRET, FakeProc


# ---- read_topology_file ---------------------------------------------------

class TestReadTopologyFile:

    def test_returns_topology_string(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager_worker"}), encoding="utf-8",
        )
        assert wizard_runtime.read_topology_file(tmp_path) == "manager_worker"

    def test_returns_empty_for_missing(self, tmp_path):
        assert wizard_runtime.read_topology_file(tmp_path) == ""

    def test_returns_empty_for_malformed(self, tmp_path):
        (tmp_path / "topology.json").write_text("not json")
        assert wizard_runtime.read_topology_file(tmp_path) == ""


# ---- spawn_runtime_for_topology -------------------------------------------

class TestSpawnRuntimeForTopology:

    def test_manager_topology_calls_bootstrap_and_spawns_manager(
        self, tmp_path,
    ):
        bootstrap = MagicMock(return_value=tmp_path / "manager")
        proc_obj = FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "manager", bootstrap, start, tmp_path,
        )
        bootstrap.assert_called_once_with(tmp_path)
        start.assert_called_once_with("manager")
        assert proc is proc_obj
        assert port == 8080

    def test_manager_worker_topology_spawns_manager(self, tmp_path):
        bootstrap = MagicMock()
        proc_obj = FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "manager_worker", bootstrap, start, tmp_path,
        )
        start.assert_called_once_with("manager")
        assert port == 8080

    def test_worker_only_topology_spawns_worker_no_port(self, tmp_path):
        bootstrap = MagicMock()
        proc_obj = FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "worker_only", bootstrap, start, tmp_path,
        )
        bootstrap.assert_not_called()
        start.assert_called_once_with("worker")
        assert port is None

    def test_worker_alias_spawns_worker(self, tmp_path):
        start = MagicMock(return_value=FakeProc())
        _, port = wizard_runtime.spawn_runtime_for_topology(
            "worker", MagicMock(), start, tmp_path,
        )
        start.assert_called_once_with("worker")
        assert port is None

    def test_unknown_topology_returns_nones(self, tmp_path):
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "bogus", MagicMock(), MagicMock(), tmp_path,
        )
        assert proc is None
        assert port is None


# ---- terminate_wizard -----------------------------------------------------

class TestTerminateWizard:

    def test_no_op_when_proc_is_none(self):
        wizard_runtime.terminate_wizard(None)

    def test_no_op_when_already_exited(self):
        proc = FakeProc(returncode=0)
        wizard_runtime.terminate_wizard(proc)
        assert proc.terminate_called is False

    def test_calls_terminate_then_wait(self):
        proc = FakeProc()
        wizard_runtime.terminate_wizard(proc)
        assert proc.terminate_called is True

    def test_kills_after_grace_timeout(self):
        proc = FakeProc()
        proc.wait_raises = True
        wizard_runtime.terminate_wizard(proc)
        assert proc.kill_called is True


# ---- hand_off_to_runtime --------------------------------------------------

class TestHandOffToRuntime:
    """Post-#203 contract: apply pipeline + wizard teardown only."""

    def test_success_path_returns_zero_and_cleans_up(
        self, tmp_path, mocker,
    ):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "worker_only"}), encoding="utf-8",
        )
        wizard_proc = FakeProc()
        cleanup = mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )
        # The apply pipeline is a no-op when there is no
        # pending_setup.json (the helper checks for the file).
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )

        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=wizard_proc,
        )
        assert rc == 0
        cleanup.assert_called_once_with(tmp_path)

    def test_returns_failure_when_topology_missing(self, tmp_path):
        rc = wizard_runtime.hand_off_to_runtime(
            payload={}, data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
        )
        assert rc == 1

    def test_terminate_wizard_called_before_cleanup_dir(
        self, tmp_path, mocker,
    ):
        """CRITICAL-2 (Phase F1): wizard MUST be terminated BEFORE
        cleanup_wizard_dir rmtrees its working directory."""
        (tmp_path / "topology.json").write_text(
            '{"topology": "worker_only"}', encoding="utf-8",
        )
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        order: list[str] = []
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
            side_effect=lambda *a, **k: order.append("terminate"),
        )
        cleanup = mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
            side_effect=lambda *a, **k: order.append("cleanup"),
        )
        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
        )
        assert order == ["terminate", "cleanup"], (
            "terminate_wizard MUST run before cleanup_wizard_dir "
            "(CRITICAL-2 Phase F1)"
        )
        terminate.assert_called_once()
        cleanup.assert_called_once_with(tmp_path)

    def test_signature_no_longer_takes_on_manager_ready(self):
        """FR-8 (v2 splash phase states): the post-handoff splash-dismiss
        callback is GONE. ``hand_off_to_runtime`` no longer accepts an
        ``on_manager_ready`` parameter — splash dismissal is now driven
        by the cold-boot health probe in ``run_wizard_mode``.
        """
        import inspect
        sig = inspect.signature(wizard_runtime.hand_off_to_runtime)
        assert "on_manager_ready" not in sig.parameters

    def test_signature_no_longer_takes_bootstrap_or_start_component(self):
        """FR-HANDOFF3 / AC-17 (issue #203): runtime spawn moved out, so
        the bootstrap_first_run and start_component params are gone."""
        import inspect
        sig = inspect.signature(wizard_runtime.hand_off_to_runtime)
        assert "bootstrap_first_run" not in sig.parameters
        assert "start_component" not in sig.parameters


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
