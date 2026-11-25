# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: ``clean --all`` (``-All`` or ``--all`` for sethlans.ps1) also
removes the per-user folder, and nothing next to it.

The macOS and XDG branches of sethlans.sh are reached on any platform with a
fake ``uname`` first on PATH; every lookup variable still points under the
fake project.
"""

from pathlib import Path

import pytest

from ._clean_asserts import (
    assert_artifacts_removed,
    assert_exit,
    assert_identity_files_kept,
    output,
    seed_identity_files,
    seed_sibling_marker,
)
from ._clean_env import script_data_dir
from ._clean_runner import own_platform, shown_path
from .conftest import clean_env, run_clean

FLAG_FORMS = [("ps1", "-All"), ("ps1", "--all"), ("pwsh", "-All"), ("pwsh", "--all"), ("sh", "--all")]


def _own_data_dir(env, shell):
    return script_data_dir(env, own_platform(shell))


def _removed_line(shell, data_dir: Path):
    return f"[OK] Shared data dir removed ({shown_path(shell, data_dir.parent, data_dir.name)})"


@pytest.mark.parametrize("shell, flag", FLAG_FORMS)
class TestAllFlagRemovesUserFolder:
    def test_removes_the_folder_and_says_so(self, fake_project, shell, flag):
        env = clean_env(fake_project)
        data_dir = _own_data_dir(env, shell)
        seed_identity_files(data_dir)

        result = run_clean(fake_project, env, [flag], shell=shell)

        assert_exit(result, 0)
        assert not data_dir.exists(), output(result)
        assert _removed_line(shell, data_dir) in result.stdout.splitlines(), output(result)
        assert "Shared data dir kept" not in result.stdout
        assert_artifacts_removed(fake_project)

    def test_spares_a_sibling_folder(self, fake_project, shell, flag):
        env = clean_env(fake_project)
        data_dir = _own_data_dir(env, shell)
        seed_identity_files(data_dir)
        marker = seed_sibling_marker(data_dir)

        result = run_clean(fake_project, env, [flag], shell=shell)

        assert_exit(result, 0)
        assert marker.read_text() == "keep me"

    def test_missing_folder_exits_zero(self, fake_project, shell, flag):
        env = clean_env(fake_project)
        data_dir = _own_data_dir(env, shell)
        assert not data_dir.exists()

        result = run_clean(fake_project, env, [flag], shell=shell)

        assert_exit(result, 0)
        assert "Shared data dir" not in result.stdout, output(result)
        assert_artifacts_removed(fake_project)
        assert not data_dir.exists()


class TestShLinuxBranch:
    """sethlans.sh with ``uname -s`` = Linux: ``${XDG_DATA_HOME:-$HOME/.local/share}/sethlans``."""

    def test_xdg_data_home_takes_precedence_over_home(self, fake_project):
        env = clean_env(fake_project)
        xdg_dir = Path(env["XDG_DATA_HOME"]) / "sethlans"
        home_dir = Path(env["HOME"]) / ".local" / "share" / "sethlans"
        seed_identity_files(xdg_dir)
        seed_identity_files(home_dir)

        result = run_clean(fake_project, env, ["--all"], shell="sh", uname="Linux")

        assert_exit(result, 0)
        assert not xdg_dir.exists(), output(result)
        assert_identity_files_kept(home_dir)
        assert _removed_line("sh", xdg_dir) in result.stdout.splitlines(), output(result)

    def test_home_is_used_when_xdg_data_home_is_unset(self, fake_project):
        env = clean_env(fake_project)
        home_dir = Path(env["HOME"]) / ".local" / "share" / "sethlans"
        seed_identity_files(home_dir)

        result = run_clean(fake_project, env, ["--all"], shell="sh", uname="Linux",
                           inner_env={"XDG_DATA_HOME": None}, unsafe_values=True)

        assert_exit(result, 0)
        assert not home_dir.exists(), output(result)
        shown = shown_path("sh", env["HOME"], ".local/share/sethlans")
        assert f"[OK] Shared data dir removed ({shown})" in result.stdout.splitlines(), output(result)

    def test_default_clean_keeps_the_xdg_folder(self, fake_project):
        env = clean_env(fake_project)
        xdg_dir = Path(env["XDG_DATA_HOME"]) / "sethlans"
        seed_identity_files(xdg_dir)

        result = run_clean(fake_project, env, shell="sh", uname="Linux")

        assert_exit(result, 0)
        assert_identity_files_kept(xdg_dir)
        assert f"[OK] Shared data dir kept ({shown_path('sh', xdg_dir.parent, 'sethlans')}); " \
            "remove it with: bash tools/sethlans.sh clean --all" in result.stdout.splitlines(), output(result)


class TestShDarwinBranch:
    """sethlans.sh with ``uname -s`` = Darwin: ``$HOME/Library/Application Support/Sethlans``."""

    def test_all_removes_the_application_support_folder(self, fake_project):
        env = clean_env(fake_project)
        data_dir = Path(env["HOME"]) / "Library" / "Application Support" / "Sethlans"
        seed_identity_files(data_dir)
        marker = seed_sibling_marker(data_dir)
        xdg_dir = Path(env["XDG_DATA_HOME"]) / "sethlans"
        seed_identity_files(xdg_dir)

        result = run_clean(fake_project, env, ["--all"], shell="sh", uname="Darwin")

        assert_exit(result, 0)
        assert not data_dir.exists(), output(result)
        assert marker.read_text() == "keep me"
        assert_identity_files_kept(xdg_dir)
        shown = shown_path("sh", env["HOME"], "Library/Application Support/Sethlans")
        assert f"[OK] Shared data dir removed ({shown})" in result.stdout.splitlines(), output(result)

    def test_default_clean_keeps_the_application_support_folder(self, fake_project):
        env = clean_env(fake_project)
        data_dir = Path(env["HOME"]) / "Library" / "Application Support" / "Sethlans"
        seed_identity_files(data_dir)

        result = run_clean(fake_project, env, shell="sh", uname="Darwin")

        assert_exit(result, 0)
        assert_identity_files_kept(data_dir)
