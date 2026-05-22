# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``launcher/apply_pending_setup.py`` — run_apply_pipeline
end-to-end and source-mode apply argv coverage.

Complements ``test_apply_pending_setup_argv.py`` (dev smoke) with the
full error-path and pipeline-sequencing matrix.
"""

from __future__ import annotations

import sys

from launcher import apply_pending_setup as apply_mod


class TestApplyPendingSetupSubprocessArgvSourceMode:
    """Source-mode argv shape for run_apply_pending_setup_subprocess.

    Counter-test to the dev's frozen-mode argv test: confirms source mode
    keeps [sys.executable, manage.py, apply_pending_setup, --data-dir, <path>]
    and that the frozen-mode path was not introduced into source mode.
    """

    def test_source_mode_argv_shape(self, mocker, tmp_path):
        data_dir = tmp_path / "manager-data"
        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )
        completed = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed,
        )

        rc, stderr = apply_mod.run_apply_pending_setup_subprocess(
            data_dir=data_dir, manager_dir=tmp_path,
        )

        assert rc == 0
        assert stderr == ""
        argv = run_mock.call_args.args[0]
        # First element is sys.executable, second is manage.py inside manager_dir
        assert argv[0] == sys.executable
        assert argv[1] == str(tmp_path / "manage.py")
        assert argv[2] == "apply_pending_setup"
        assert argv[3] == "--data-dir"
        assert argv[4] == str(data_dir)
        # Ensure no --manage flag bleeds into source-mode argv
        assert "--manage" not in argv


class TestRunApplyPipelineFrozenModeEndToEnd:
    """FR-LAUNCHER1 end-to-end: mock subprocess.run to simulate a clean
    frozen-mode pipeline and verify run_apply_pipeline returns (True, "")
    with both subprocesses invoked using frozen-mode argv shapes.
    """

    def test_frozen_mode_both_steps_succeed(self, mocker, tmp_path):
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        # Both subprocess.run calls succeed with rc=0
        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

        ok, message = apply_mod.run_apply_pipeline(data_dir)

        assert ok is True
        assert message == ""
        assert run_mock.call_count == 2

        # First call: migrate
        first_argv = run_mock.call_args_list[0].args[0]
        assert first_argv == [str(fake_exe), "--manage", "migrate", "--noinput"]

        # Second call: apply_pending_setup with --data-dir
        second_argv = run_mock.call_args_list[1].args[0]
        assert second_argv == [
            str(fake_exe), "--manage", "apply_pending_setup",
            "--data-dir", str(data_dir),
        ]


class TestRunApplyPipelineMigrateFailure:
    """FR-APPLY-ORDERING: if migrate fails, apply_pending_setup is NOT called
    and run_apply_pipeline returns (False, 'migrate failed ...').

    This is the short-circuit contract — the launcher must never spawn the
    manager runtime when migrate fails.
    """

    def test_migrate_failure_short_circuits(self, mocker, tmp_path):
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        # migrate fails with non-zero exit code
        failed = mocker.MagicMock(returncode=1, stdout="", stderr="migrate error")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=failed,
        )

        ok, message = apply_mod.run_apply_pipeline(data_dir)

        assert ok is False
        assert "migrate failed" in message
        assert "1" in message  # exit code is reported
        # Only migrate was called — apply_pending_setup was not
        assert run_mock.call_count == 1

    def test_migrate_failure_exit_code_in_message(self, mocker, tmp_path):
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        exit_code = 42
        failed = mocker.MagicMock(returncode=exit_code, stdout="", stderr="")
        mocker.patch.object(
            apply_mod.subprocess, "run", return_value=failed,
        )

        ok, message = apply_mod.run_apply_pipeline(data_dir)

        assert ok is False
        assert str(exit_code) in message


class TestRunApplyPipelineApplyFailure:
    """Migrate succeeds but apply_pending_setup returns non-zero.

    run_apply_pipeline must return (False, stderr.strip()) when apply fails.
    """

    def test_apply_failure_returns_stderr(self, mocker, tmp_path):
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        migrate_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        apply_err = mocker.MagicMock(
            returncode=1, stdout="", stderr="  pending setup validation failed  ",
        )
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run",
            side_effect=[migrate_ok, apply_err],
        )

        ok, message = apply_mod.run_apply_pipeline(data_dir)

        assert ok is False
        # stderr is stripped per spec
        assert message == "pending setup validation failed"
        assert run_mock.call_count == 2

    def test_apply_failure_without_stderr_uses_exit_code(self, mocker, tmp_path):
        """When apply_pending_setup fails with no stderr, message uses
        the exit code as a fallback."""
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=tmp_path)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        migrate_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        apply_err = mocker.MagicMock(returncode=3, stdout="", stderr="")
        mocker.patch.object(
            apply_mod.subprocess, "run",
            side_effect=[migrate_ok, apply_err],
        )

        ok, message = apply_mod.run_apply_pipeline(data_dir)

        assert ok is False
        assert "3" in message  # fallback includes exit code


def _flatten_log_args(call_args_list):
    """Return a flat string of all positional args from a list of mock calls.

    Uses the actual call args (not str(call)) to avoid Windows backslash
    double-escaping that occurs when stringifying call objects.
    """
    parts = []
    for call in call_args_list:
        for arg in call.args:
            parts.append(str(arg) if not isinstance(arg, list) else " ".join(str(a) for a in arg))
    return " ".join(parts)


class TestLoggerEmitsArgvInFrozenMode:
    """FR-LAUNCHER3: in frozen mode the logger must emit enough information
    to make debugging frozen-mode regressions trivial.

    Asserts that the logged message for migrate contains the manager-exe
    path, '--manage', and the subcommand name.
    """

    def test_logger_emits_full_argv_frozen_mode_migrate(
        self, mocker, tmp_path,
    ):
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed = mocker.MagicMock(returncode=0, stdout="", stderr="")
        mocker.patch.object(apply_mod.subprocess, "run", return_value=completed)

        log_info = mocker.patch.object(apply_mod.logger, "info")

        apply_mod.run_migrate_subprocess(manager_dir=tmp_path)

        # Flatten actual call args (not str(call)) to avoid Windows double-escaping
        combined = _flatten_log_args(log_info.call_args_list)
        assert str(fake_exe) in combined, (
            f"Logger must emit the manager-exe path in frozen mode. Got: {combined}"
        )
        assert "--manage" in combined, (
            f"Logger must emit '--manage' in frozen mode. Got: {combined}"
        )
        assert "migrate" in combined, (
            f"Logger must emit the subcommand name in frozen mode. Got: {combined}"
        )

    def test_logger_emits_full_argv_frozen_mode_apply(
        self, mocker, tmp_path,
    ):
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("fake", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed = mocker.MagicMock(returncode=0, stdout="", stderr="")
        mocker.patch.object(apply_mod.subprocess, "run", return_value=completed)

        log_info = mocker.patch.object(apply_mod.logger, "info")

        apply_mod.run_apply_pending_setup_subprocess(
            data_dir=data_dir, manager_dir=tmp_path,
        )

        combined = _flatten_log_args(log_info.call_args_list)
        assert str(fake_exe) in combined, (
            f"Logger must emit the manager-exe path in frozen mode. Got: {combined}"
        )
        assert "--manage" in combined, (
            f"Logger must emit '--manage' in frozen mode. Got: {combined}"
        )
        assert "apply_pending_setup" in combined, (
            f"Logger must emit the subcommand name in frozen mode. Got: {combined}"
        )
