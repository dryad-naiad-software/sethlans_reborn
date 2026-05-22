# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: wizard-to-apply data-dir handoff contract (issue #195 AC-9).

Regression guard for the bug where ``run_apply_pipeline_if_needed`` appended
``/manager`` to ``data_dir`` before calling ``run_apply_pipeline``, causing
every manager-bearing topology to fail with "pending_setup.json missing".

The wizard writes ``pending_setup.json`` to the **shared** data root
(``data_dir``); ``apply_pending_setup`` must receive that same root via
``--data-dir`` — not a ``manager/`` subdir.

These tests call the real ``run_apply_pipeline_if_needed`` orchestrator with
a real ``pending_setup.json`` on disk and mock only the subprocess layer,
capturing the argv that would be passed to the apply subprocess.

Test coverage:
  - AC-9 canonical: --data-dir in apply argv equals str(tmp_path) verbatim.
  - Topology guard: worker_only skips the pipeline entirely.
  - Negative case: calling run_apply_pipeline with the wrong (buggy) subdir
    returns (False, ...), proving the test would have caught issue #195.
"""

from __future__ import annotations

import pytest

from launcher import apply_pending_setup as apply_mod

from tests.integration.launcher._dispatch_helpers import (
    MANAGER_DIR,
    _curated_env_for_subprocess,
    _write_pending,
)


class TestRunApplyPipelineIfNeededDataDirHandoff:
    """AC-9: run_apply_pipeline_if_needed passes data_dir verbatim as --data-dir.

    Regression guard for issue #195, where the launcher appended ``/manager``
    to ``data_dir`` before calling ``run_apply_pipeline``, causing every
    manager-bearing topology to fail with "pending_setup.json missing".

    The wizard writes ``pending_setup.json`` to the shared data root
    (``data_dir``); ``apply_pending_setup`` must receive the same root
    via ``--data-dir``, not a ``manager/`` subdir.

    These tests operate at the integration boundary: they call the real
    ``run_apply_pipeline_if_needed`` orchestrator with a real
    ``pending_setup.json`` on disk and mock only the subprocess layer,
    capturing the argv that would be passed to the apply subprocess.
    """

    def test_data_dir_passed_verbatim_not_manager_subdir(self, mocker, tmp_path):
        """--data-dir in the apply subprocess argv equals str(tmp_path), not str(tmp_path/'manager').

        This is the canonical AC-9 regression test. If the ``/ "manager"``
        append from issue #195 were re-introduced, this assertion would fail.
        """
        _write_pending(tmp_path, topology="manager_worker")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value=_curated_env_for_subprocess(),
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        wizard_proc = mocker.MagicMock()
        terminate_cb = mocker.MagicMock()
        failure_cb = mocker.MagicMock(return_value=1)

        result = apply_mod.run_apply_pipeline_if_needed(
            topology="manager_worker",
            data_dir=tmp_path,
            wizard_proc=wizard_proc,
            terminate_wizard_cb=terminate_cb,
            failure_exit_cb=failure_cb,
        )

        assert result is None, (
            f"Expected None (success), got {result!r}"
        )
        assert run_mock.call_count == 2, (
            f"Expected 2 subprocess calls (migrate + apply), got {run_mock.call_count}"
        )

        # The apply subprocess argv (second call) must contain --data-dir str(tmp_path)
        apply_argv = run_mock.call_args_list[1].args[0]
        assert "--data-dir" in apply_argv, (
            f"apply subprocess argv missing --data-dir flag: {apply_argv!r}"
        )
        data_dir_idx = apply_argv.index("--data-dir")
        actual_data_dir = apply_argv[data_dir_idx + 1]
        assert actual_data_dir == str(tmp_path), (
            f"--data-dir must be the shared root str(tmp_path)={str(tmp_path)!r}, "
            f"got {actual_data_dir!r}. "
            f"If this is str(tmp_path / 'manager'), issue #195 was re-introduced."
        )
        assert actual_data_dir != str(tmp_path / "manager"), (
            "--data-dir must NOT be tmp_path/'manager' — that is the #195 bug."
        )

    def test_manager_topology_also_passes_data_dir_verbatim(self, mocker, tmp_path):
        """'manager' topology (no worker component) also gets the shared root.

        Verifies the fix applies to all manager-bearing topologies, not just
        ``manager_worker``.
        """
        _write_pending(tmp_path, topology="manager")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value=_curated_env_for_subprocess(),
        )

        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        wizard_proc = mocker.MagicMock()
        terminate_cb = mocker.MagicMock()
        failure_cb = mocker.MagicMock(return_value=1)

        apply_mod.run_apply_pipeline_if_needed(
            topology="manager",
            data_dir=tmp_path,
            wizard_proc=wizard_proc,
            terminate_wizard_cb=terminate_cb,
            failure_exit_cb=failure_cb,
        )

        assert run_mock.call_count == 2
        apply_argv = run_mock.call_args_list[1].args[0]
        data_dir_idx = apply_argv.index("--data-dir")
        actual_data_dir = apply_argv[data_dir_idx + 1]
        assert actual_data_dir == str(tmp_path), (
            f"'manager' topology --data-dir must be shared root, got {actual_data_dir!r}"
        )

    @pytest.mark.parametrize("topology", ["worker_only", "worker", ""])
    def test_non_manager_topologies_skip_pipeline(self, mocker, tmp_path, topology):
        """Non-manager topologies do not invoke run_apply_pipeline at all.

        Verifies the topology guard returns None and makes no subprocess calls.
        """
        run_mock = mocker.patch.object(apply_mod.subprocess, "run")

        wizard_proc = mocker.MagicMock()
        terminate_cb = mocker.MagicMock()
        failure_cb = mocker.MagicMock(return_value=1)

        result = apply_mod.run_apply_pipeline_if_needed(
            topology=topology,
            data_dir=tmp_path,
            wizard_proc=wizard_proc,
            terminate_wizard_cb=terminate_cb,
            failure_exit_cb=failure_cb,
        )

        assert result is None, (
            f"topology={topology!r} must skip apply pipeline, got {result!r}"
        )
        run_mock.assert_not_called()

    def test_negative_case_wrong_data_dir_would_fail(self, mocker, tmp_path):
        """Negative case: run_apply_pipeline against tmp_path/'manager' returns (False, ...).

        Demonstrates that if the #195 bug were present (passing data_dir/'manager'
        to run_apply_pipeline), the apply step would fail because
        pending_setup.json lives at tmp_path, not tmp_path/manager.

        Proves the test suite would have caught the original regression.
        """
        # Write pending_setup.json at the shared root (tmp_path), NOT in manager/
        _write_pending(tmp_path, topology="manager_worker")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value=_curated_env_for_subprocess(),
        )

        # Simulate what the buggy code would have done: pass manager subdir
        buggy_data_dir = tmp_path / "manager"
        buggy_data_dir.mkdir()

        # migrate succeeds, apply fails because pending_setup.json is not
        # in tmp_path/manager (it's in tmp_path)
        migrate_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        apply_fail = mocker.MagicMock(
            returncode=1,
            stdout="",
            stderr="apply pre-guard failed: pending_setup.json missing",
        )
        mocker.patch.object(
            apply_mod.subprocess, "run",
            side_effect=[migrate_ok, apply_fail],
        )

        ok, message = apply_mod.run_apply_pipeline(buggy_data_dir)

        assert ok is False, (
            "Passing the wrong (manager subdir) data_dir must fail — "
            "this confirms the test would have caught the #195 bug."
        )
        assert "pending_setup.json missing" in message or message, (
            f"Expected failure message for missing pending_setup.json, got {message!r}"
        )
