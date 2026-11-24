# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #311: the clean-script tests must never reach the developer's real
per-user Sethlans folder.

``clean`` removes ``%LOCALAPPDATA%\\Sethlans`` (sethlans.ps1) or the macOS /
XDG equivalent (sethlans.sh). These tests check that run_clean() points the
child at a per-test folder, that the script removes the redirected folder and
nothing next to it, and that an environment reaching anywhere else is refused
before a child is spawned.
"""

import os
import sys
from pathlib import Path

import pytest

from . import _clean_env
from ._clean_env import (
    UnsafeCleanEnvironment,
    assert_env_isolated,
    isolated_env,
    script_data_dir,
)
from .conftest import IS_WINDOWS, clean_env, run_clean, user_dirs_base


def _assert_ok(result):
    assert result.returncode == 0, (
        f"clean exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _seed_identity_files(data_dir: Path):
    """Stand-ins for the real identity files clean would wipe."""
    (data_dir / "manager").mkdir(parents=True)
    (data_dir / "manager" / "secret_key").write_text("fake secret")
    (data_dir / "worker").mkdir()
    (data_dir / "worker" / "config.json").write_text('{"api_token": "fake"}')


def _seed_sibling_marker(data_dir: Path) -> Path:
    marker = data_dir.parent / "UnrelatedApp" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me")
    return marker


class TestCleanRemovesOnlyRedirectedFolder:
    """Real script runs, with the per-user variables under tmp_path."""

    def test_removes_redirected_folder_and_spares_sibling(self, fake_project):
        env = clean_env(fake_project)
        data_dir = assert_env_isolated(env, user_dirs_base(fake_project))
        _seed_identity_files(data_dir)
        marker = _seed_sibling_marker(data_dir)

        result = run_clean(fake_project, env)

        _assert_ok(result)
        assert not data_dir.exists(), "clean should remove the redirected per-user folder"
        assert marker.read_text() == "keep me", "clean must not touch a sibling folder"
        assert f"Shared data dir removed ({data_dir})" in result.stdout

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG lookup is the Linux branch")
    def test_xdg_data_home_wins_over_home_on_linux(self, fake_project):
        env = clean_env(fake_project)
        xdg_dir = Path(env["XDG_DATA_HOME"]) / "sethlans"
        home_dir = Path(env["HOME"]) / ".local" / "share" / "sethlans"
        _seed_identity_files(xdg_dir)
        _seed_identity_files(home_dir)

        result = run_clean(fake_project, env)

        _assert_ok(result)
        assert not xdg_dir.exists()
        assert (home_dir / "manager" / "secret_key").read_text() == "fake secret"


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
    @pytest.mark.parametrize("platform, names", [
        ("win32", ["LOCALAPPDATA"]),
        ("darwin", ["HOME", "XDG_DATA_HOME"]),
        ("linux", ["HOME", "XDG_DATA_HOME"]),
    ])
    def test_points_every_lookup_variable_under_base(self, tmp_path, platform, names):
        env = isolated_env(tmp_path, platform)

        for name in names:
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
