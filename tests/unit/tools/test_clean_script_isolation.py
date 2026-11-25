# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #311: the clean-script tests must never reach the developer's real
per-user Sethlans folder.

``clean --all`` (``-All``) removes ``%LOCALAPPDATA%\\Sethlans`` (sethlans.ps1)
or the macOS / XDG / Git Bash equivalent (sethlans.sh); a plain ``clean``
keeps it (issue #315). These tests check that run_clean() points the child at
a per-test folder, that the flag form removes the redirected folder and
nothing next to it, that the default form keeps it, and that an environment
reaching anywhere else is refused before a child is spawned.
"""

import os
from pathlib import Path

import pytest

from . import _clean_env
from ._clean_asserts import (
    assert_exit,
    assert_identity_files_kept,
    seed_identity_files,
    seed_sibling_marker,
)
from ._clean_env import (
    UnsafeCleanEnvironment,
    assert_env_isolated,
    assert_vars_safe,
    isolated_env,
    script_data_dir,
)
from ._clean_runner import shown_path
from .conftest import DEFAULT_SHELL, IS_WINDOWS, clean_env, run_clean, user_dirs_base

_ALL_FLAG = "-All" if DEFAULT_SHELL == "ps1" else "--all"


class TestCleanRemovesOnlyRedirectedFolder:
    """Real script runs, with the per-user variables under tmp_path."""

    def test_removes_redirected_folder_and_spares_sibling(self, fake_project):
        env = clean_env(fake_project)
        data_dir = assert_env_isolated(env, user_dirs_base(fake_project))
        seed_identity_files(data_dir)
        marker = seed_sibling_marker(data_dir)

        result = run_clean(fake_project, env, [_ALL_FLAG])

        assert_exit(result, 0)
        assert not data_dir.exists(), "clean --all should remove the redirected per-user folder"
        assert marker.read_text() == "keep me", "clean must not touch a sibling folder"
        shown = shown_path(DEFAULT_SHELL, data_dir.parent, data_dir.name)
        assert f"Shared data dir removed ({shown})" in result.stdout

    def test_default_clean_keeps_redirected_folder(self, fake_project):
        env = clean_env(fake_project)
        data_dir = assert_env_isolated(env, user_dirs_base(fake_project))
        seed_identity_files(data_dir)

        result = run_clean(fake_project, env)

        assert_exit(result, 0)
        assert_identity_files_kept(data_dir)
        assert "Shared data dir removed" not in result.stdout


class TestScriptDataDir:
    """script_data_dir() mirrors each script's own lookup."""

    @pytest.mark.parametrize("platform, env, expected", [
        ("win32", {"LOCALAPPDATA": "/data/local"}, Path("/data/local") / "Sethlans"),
        ("darwin", {"HOME": "/h"}, Path("/h/Library/Application Support/Sethlans")),
        ("linux", {"HOME": "/h", "XDG_DATA_HOME": "/x"}, Path("/x/sethlans")),
        ("linux", {"HOME": "/h", "XDG_DATA_HOME": ""}, Path("/h/.local/share/sethlans")),
        ("linux", {"HOME": "/h"}, Path("/h/.local/share/sethlans")),
    ])
    def test_matches_script_lookup(self, platform, env, expected):
        assert script_data_dir(env, platform) == expected

    @pytest.mark.parametrize("platform, env", [
        ("win32", {}),
        ("win32", {"LOCALAPPDATA": ""}),
        ("darwin", {}),
        ("linux", {}),
    ])
    def test_missing_variable_gives_none(self, platform, env):
        assert script_data_dir(env, platform) is None


class TestIsolatedEnv:
    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_points_every_lookup_variable_under_base(self, tmp_path, platform):
        env = isolated_env(tmp_path, platform)

        for name in ("LOCALAPPDATA", "HOME", "XDG_DATA_HOME"):
            assert Path(env[name]).is_dir()
            assert Path(env[name]).parent == tmp_path
        assert tmp_path in script_data_dir(env, platform).parents

    def test_keeps_the_rest_of_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SETHLANS_TEST_MARKER", "kept")

        assert isolated_env(tmp_path)["SETHLANS_TEST_MARKER"] == "kept"


class TestGuard:
    """assert_env_isolated() refuses anything outside the per-test folder."""

    def test_accepts_isolated_env(self, tmp_path):
        env = isolated_env(tmp_path)

        target = assert_env_isolated(env, tmp_path)

        assert target == script_data_dir(env)
        assert tmp_path in target.parents

    def test_rejects_the_inherited_environment(self, tmp_path):
        with pytest.raises(UnsafeCleanEnvironment):
            assert_env_isolated(dict(_clean_env._PARENT_ENV), tmp_path)

    def test_rejects_env_outside_base(self, tmp_path):
        env = isolated_env(tmp_path / "elsewhere")

        with pytest.raises(UnsafeCleanEnvironment, match="outside the per-test folder"):
            assert_env_isolated(env, tmp_path / "user-dirs")

    def test_rejects_env_reaching_a_real_folder(self, tmp_path, mocker):
        # Pretend the per-test folder is a real per-user folder: the overlap
        # check must refuse it even though it is also inside ``base``.
        env = isolated_env(tmp_path)
        mocker.patch.object(_clean_env, "real_user_data_dirs", return_value={tmp_path})

        with pytest.raises(UnsafeCleanEnvironment, match="real per-user folder"):
            assert_env_isolated(env, tmp_path)

    def test_rejects_env_without_lookup_variable(self, tmp_path):
        env = isolated_env(tmp_path)
        for name in ("LOCALAPPDATA", "HOME", "XDG_DATA_HOME"):
            env.pop(name, None)

        with pytest.raises(UnsafeCleanEnvironment, match="no per-user folder variable"):
            assert_env_isolated(env, tmp_path)


class TestRunCleanEnvironment:
    """run_clean() never spawns a child with an unsafe environment."""

    def test_default_child_env_is_isolated(self, fake_project, mocker):
        run = mocker.patch("subprocess.run")

        run_clean(fake_project)

        child_env = run.call_args.kwargs["env"]
        assert user_dirs_base(fake_project) in script_data_dir(child_env).parents
        name = "LOCALAPPDATA" if IS_WINDOWS else "HOME"
        assert child_env[name] != os.environ.get(name)

    def test_refuses_inherited_env_without_spawning(self, fake_project, mocker):
        run = mocker.patch("subprocess.run")

        with pytest.raises(UnsafeCleanEnvironment):
            run_clean(fake_project, dict(os.environ))

        run.assert_not_called()

    def test_passes_extra_arguments_after_the_command(self, fake_project, mocker):
        run = mocker.patch("subprocess.run")

        run_clean(fake_project, args=[_ALL_FLAG])

        assert run.call_args.args[0][-2:] == ["clean", _ALL_FLAG]

    @pytest.mark.parametrize("name", ["LOCALAPPDATA", "HOME", "XDG_DATA_HOME"])
    def test_refuses_any_lookup_variable_outside_base(self, fake_project, tmp_path_factory, mocker, name):
        run = mocker.patch("subprocess.run")
        env = clean_env(fake_project)
        env[name] = str(tmp_path_factory.mktemp("elsewhere"))

        with pytest.raises(UnsafeCleanEnvironment, match=name):
            run_clean(fake_project, env)

        run.assert_not_called()

    def test_unsafe_values_mode_still_refuses_absolute_outside_base(self, fake_project, tmp_path_factory, mocker):
        run = mocker.patch("subprocess.run")
        env = clean_env(fake_project)
        env["HOME"] = str(tmp_path_factory.mktemp("elsewhere"))

        with pytest.raises(UnsafeCleanEnvironment, match="HOME"):
            run_clean(fake_project, env, unsafe_values=True)

        run.assert_not_called()


class TestAssertVarsSafe:
    @pytest.mark.parametrize("value", [None, "", "relative"])
    def test_unsafe_values_mode_allows_unset_empty_relative(self, tmp_path, value):
        env = isolated_env(tmp_path)
        if value is None:
            env.pop("HOME")
        else:
            env["HOME"] = value

        assert_vars_safe(env, tmp_path, allow_unsafe_values=True)

        with pytest.raises(UnsafeCleanEnvironment, match="HOME"):
            assert_vars_safe(env, tmp_path)

    def test_refuses_value_reaching_a_real_folder(self, tmp_path, mocker):
        env = isolated_env(tmp_path)
        mocker.patch.object(_clean_env, "real_user_data_dirs", return_value={tmp_path / "home"})

        with pytest.raises(UnsafeCleanEnvironment, match="real per-user folder"):
            assert_vars_safe(env, tmp_path)
