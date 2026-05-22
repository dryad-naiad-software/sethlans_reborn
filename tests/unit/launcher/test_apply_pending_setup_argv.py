# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for #191 frozen-mode argv in ``launcher/apply_pending_setup.py``.

The full per-branch matrix lives in the unit-test agent's deliverable;
this file is the minimum the dev stage needs to prove the implementation
shape is correct.
"""

from __future__ import annotations

from pathlib import Path

from launcher import apply_pending_setup as apply_mod


class TestRunMigrateSubprocessArgv:

    def test_frozen_mode_uses_manage_dispatcher(self, mocker, tmp_path):
        """Frozen mode: argv must be ``[run_manager.exe, --manage,
        migrate, --noinput]`` — NOT ``[sys.executable, manage.py, ...]``.

        This is the exact regression #191 introduced: in frozen mode
        ``sys.executable`` is ``run_launcher.exe`` whose argparse rejects
        the ``manage.py`` path with exit 2.
        """
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("not a real exe", encoding="utf-8")

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed,
        )

        rc = apply_mod.run_migrate_subprocess(manager_dir=tmp_path)

        assert rc == 0
        argv = run_mock.call_args.args[0]
        assert argv == [
            str(fake_exe), "--manage", "migrate", "--noinput",
        ], f"frozen-mode argv shape regressed: {argv}"

    def test_source_mode_uses_sys_executable_plus_manage_py(
        self, mocker, tmp_path,
    ):
        """Source mode argv stays ``[sys.executable, manage.py, migrate,
        --noinput]`` — proves the frozen-mode branch did not poison the
        source-mode path."""
        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(
            apply_mod, "build_curated_env", return_value={"PATH": "/usr/bin"},
        )

        completed = mocker.MagicMock(returncode=0, stdout="", stderr="")
        run_mock = mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed,
        )

        apply_mod.run_migrate_subprocess(manager_dir=tmp_path)

        argv = run_mock.call_args.args[0]
        # First entry is sys.executable (some Python interpreter),
        # second is the manage.py inside the manager_dir.
        assert argv[1] == str(tmp_path / "manage.py")
        assert argv[2:] == ["migrate", "--noinput"]
        assert "--manage" not in argv

    def test_apply_pending_setup_frozen_mode_argv(self, mocker, tmp_path):
        """Apply-pending-setup frozen branch carries ``--data-dir``."""
        fake_exe = tmp_path / "run_manager.exe"
        fake_exe.write_text("not a real exe", encoding="utf-8")
        data_dir = tmp_path / "manager-data"

        mocker.patch.object(apply_mod, "_is_frozen", return_value=True)
        mocker.patch.object(apply_mod, "_manager_exe", return_value=fake_exe)
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
        assert argv == [
            str(fake_exe), "--manage", "apply_pending_setup",
            "--data-dir", str(data_dir),
        ], f"frozen-mode argv shape regressed: {argv}"


class TestManagerExeResolver:
    """Source-mode guard on the manager-exe resolver.

    The resolver MUST raise in source mode so callers cannot
    accidentally hit the frozen-mode argv branch in dev.
    """

    def test_source_mode_raises_runtime_error(self, mocker):
        from launcher import manager_exe_resolver as mer
        mocker.patch.object(mer, "is_frozen", return_value=False)
        try:
            mer.manager_exe()
        except RuntimeError as exc:
            assert "source mode" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(
                "manager_exe() should raise RuntimeError in source mode"
            )

    def test_frozen_mode_resolves_and_returns_path(
        self, mocker, tmp_path,
    ):
        """Happy path: frozen mode + a real file at the resolved location
        returns the resolved path and the bounds check passes."""
        from launcher import manager_exe_resolver as mer
        import sys as _sys

        # Build a fake install tree: <app>/run_manager(.exe) and
        # frozen_paths points get_manager_dir at <app>.
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        exe_name = "run_manager.exe" if _sys.platform == "win32" else "run_manager"
        exe = app_dir / exe_name
        exe.write_text("fake", encoding="utf-8")

        mocker.patch.object(mer, "is_frozen", return_value=True)
        # The lazy import inside manager_exe() reads frozen_paths;
        # patch both helpers there.
        import shared.frozen_paths as fp
        mocker.patch.object(fp, "get_manager_dir", return_value=app_dir)
        mocker.patch.object(fp, "get_app_dir", return_value=app_dir)

        resolved = mer.manager_exe()
        assert resolved == Path(exe).resolve()
