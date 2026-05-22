# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``run_apply_pipeline_if_needed`` in
``launcher/apply_pending_setup.py`` (issue #195, AC-4).

Verifies that the shared data root is passed verbatim to
``run_apply_pipeline`` — no ``/manager`` suffix appended.
"""

from __future__ import annotations

from launcher import apply_pending_setup as apply_mod


class TestRunApplyPipelineIfNeededPassesSharedDataDir:
    """AC-4: run_apply_pipeline_if_needed passes data_dir verbatim to
    run_apply_pipeline — no /manager suffix appended (issue #195 fix).
    """

    def test_passes_shared_data_dir_not_manager_subdir(self, mocker, tmp_path):
        """data_dir (shared root) is threaded verbatim into run_apply_pipeline."""
        data_dir = tmp_path / "shared"
        data_dir.mkdir()

        captured = {}

        def fake_run_apply_pipeline(d):
            captured["data_dir"] = d
            return True, ""

        mocker.patch.object(
            apply_mod, "run_apply_pipeline", side_effect=fake_run_apply_pipeline,
        )

        result = apply_mod.run_apply_pipeline_if_needed(
            topology="manager_worker",
            data_dir=data_dir,
            wizard_proc=None,
            terminate_wizard_cb=mocker.MagicMock(),
            failure_exit_cb=mocker.MagicMock(),
        )

        assert result is None, "success must return None"
        assert captured["data_dir"] == data_dir, (
            f"run_apply_pipeline must receive the shared root, got: {captured['data_dir']}"
        )
        # Confirm no /manager suffix was appended
        assert captured["data_dir"] != data_dir / "manager", (
            "run_apply_pipeline must NOT receive data_dir / 'manager'"
        )

    def test_worker_only_topology_skips_pipeline(self, mocker, tmp_path):
        """worker-only topology must not invoke run_apply_pipeline at all."""
        pipeline_mock = mocker.patch.object(apply_mod, "run_apply_pipeline")

        result = apply_mod.run_apply_pipeline_if_needed(
            topology="worker",
            data_dir=tmp_path,
            wizard_proc=None,
            terminate_wizard_cb=mocker.MagicMock(),
            failure_exit_cb=mocker.MagicMock(),
        )

        assert result is None
        pipeline_mock.assert_not_called()

    def test_manager_topology_also_passes_shared_data_dir(self, mocker, tmp_path):
        """'manager' topology (manager-only) also passes data_dir verbatim."""
        data_dir = tmp_path

        captured = {}

        def fake_run_apply_pipeline(d):
            captured["data_dir"] = d
            return True, ""

        mocker.patch.object(
            apply_mod, "run_apply_pipeline", side_effect=fake_run_apply_pipeline,
        )

        apply_mod.run_apply_pipeline_if_needed(
            topology="manager",
            data_dir=data_dir,
            wizard_proc=None,
            terminate_wizard_cb=mocker.MagicMock(),
            failure_exit_cb=mocker.MagicMock(),
        )

        assert captured["data_dir"] == data_dir
        assert captured["data_dir"] != data_dir / "manager"

    def test_failure_calls_callbacks_and_returns_exit_code(self, mocker, tmp_path):
        """When the pipeline fails, terminate_wizard_cb is called and
        failure_exit_cb's return value is propagated."""
        wizard_proc = mocker.MagicMock()
        terminate_cb = mocker.MagicMock()
        failure_cb = mocker.MagicMock(return_value=1)

        mocker.patch.object(
            apply_mod, "run_apply_pipeline", return_value=(False, "something failed"),
        )

        result = apply_mod.run_apply_pipeline_if_needed(
            topology="manager_worker",
            data_dir=tmp_path,
            wizard_proc=wizard_proc,
            terminate_wizard_cb=terminate_cb,
            failure_exit_cb=failure_cb,
        )

        assert result == 1
        terminate_cb.assert_called_once_with(wizard_proc)
        failure_cb.assert_called_once_with("apply_pending_setup_failed")
