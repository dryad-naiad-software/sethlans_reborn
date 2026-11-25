# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: a plain ``clean`` removes only the repo artifacts.

The per-user folder holds the identity files of any installed manager or
worker on the account, so ``clean`` without ``--all`` / ``-All`` keeps it and
says how to remove it, and says nothing when there is no folder. Each test
runs both scripts where their shell exists; ``sethlans.sh`` takes its own
platform branch (Git Bash: ``LOCALAPPDATA``; macOS: ``HOME``; else XDG).
"""

import pytest

from ._clean_asserts import (
    KEEP_HINT,
    assert_artifacts_removed,
    assert_exit,
    assert_identity_files_kept,
    assert_no_folder_message,
    output,
    seed_identity_files,
)
from ._clean_env import script_data_dir
from ._clean_runner import PS_SHELLS, SHELLS, own_platform, script_kind, shown_path
from .conftest import IS_WINDOWS, clean_env, run_clean


def own_branch_data_dir(env, shell):
    """The per-user folder the script finds on this machine's own branch."""
    return script_data_dir(env, own_platform(shell))


@pytest.mark.parametrize("shell", SHELLS)
class TestDefaultCleanKeepsUserFolder:
    def test_removes_repo_artifacts_and_keeps_user_folder(self, fake_project, shell):
        env = clean_env(fake_project)
        data_dir = own_branch_data_dir(env, shell)
        seed_identity_files(data_dir)

        result = run_clean(fake_project, env, shell=shell)

        assert_exit(result, 0)
        assert_artifacts_removed(fake_project)
        assert_identity_files_kept(data_dir)
        assert "Shared data dir removed" not in result.stdout

    def test_says_where_the_kept_folder_is_and_how_to_remove_it(self, fake_project, shell):
        env = clean_env(fake_project)
        data_dir = own_branch_data_dir(env, shell)
        seed_identity_files(data_dir)

        result = run_clean(fake_project, env, shell=shell)

        shown = shown_path(shell, data_dir.parent, data_dir.name)
        expected = f"[OK] Shared data dir kept ({shown}); remove it with: {KEEP_HINT[script_kind(shell)]}"
        assert expected in result.stdout.splitlines(), output(result)

    def test_prints_nothing_about_a_missing_folder(self, fake_project, shell):
        env = clean_env(fake_project)
        assert not own_branch_data_dir(env, shell).exists()

        result = run_clean(fake_project, env, shell=shell)

        assert_exit(result, 0)
        assert_artifacts_removed(fake_project)
        assert_no_folder_message(result)

    def test_a_file_in_place_of_the_folder_is_kept_without_a_message(self, fake_project, shell):
        env = clean_env(fake_project)
        data_dir = own_branch_data_dir(env, shell)
        # The macOS parent (home/Library/Application Support) does not exist yet.
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        data_dir.write_text("not a folder")

        result = run_clean(fake_project, env, shell=shell)

        assert_exit(result, 0)
        assert data_dir.read_text() == "not a folder"
        assert_no_folder_message(result)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("value", ["", None], ids=["empty", "unset"])
def test_default_clean_without_localappdata_still_cleans_the_repo(fake_project, shell, value):
    """Only --all needs a safe folder; a plain clean runs and says nothing about it."""
    if shell == "sh" and not IS_WINDOWS:
        pytest.skip("the LOCALAPPDATA lookup is the Git Bash branch")
    env = clean_env(fake_project)
    inner_env = None
    if shell in PS_SHELLS:
        if value is None:
            env.pop("LOCALAPPDATA")
        else:
            env["LOCALAPPDATA"] = value
    else:
        inner_env = {"LOCALAPPDATA": value}

    result = run_clean(fake_project, env, shell=shell, inner_env=inner_env, unsafe_values=True)

    assert_exit(result, 0)
    assert_artifacts_removed(fake_project)
    assert_no_folder_message(result)
