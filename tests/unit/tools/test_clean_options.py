# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: option errors in both scripts.

Only ``clean`` takes an option (``--all``; ``-All`` too in sethlans.ps1). An
unknown option for ``clean`` and any option on another command exit 1 with a
clear error, before anything is stopped or deleted. sethlans.ps1 with ``-All``
and no command gives the normal usage error.
"""

import pytest

from ._clean_asserts import (
    assert_artifacts_untouched,
    assert_exit,
    assert_identity_files_kept,
    output,
    seed_identity_files,
)
from ._clean_env import script_data_dir
from ._clean_runner import PS_SHELLS, SHELLS, own_platform
from .conftest import clean_env, run_clean


def _lines(result):
    return [line.strip() for line in result.stdout.splitlines()]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("extra", [["--bogus"], ["--all", "--bogus"]], ids=["alone", "after-all"])
def test_unknown_clean_option_is_rejected_and_nothing_is_deleted(fake_project, shell, extra):
    env = clean_env(fake_project)
    data_dir = script_data_dir(env, own_platform(shell))
    seed_identity_files(data_dir)

    result = run_clean(fake_project, env, extra, shell=shell)

    assert_exit(result, 1)
    assert "[ERROR] Unknown option for clean: --bogus" in _lines(result), output(result)
    assert "Sethlans Reborn -- Clean" not in result.stdout
    assert_artifacts_untouched(fake_project)
    assert_identity_files_kept(data_dir)


_PS_OTHER_COMMANDS = [
    (shell, command, extra)
    for shell in PS_SHELLS
    for command, extra in (("status", ["--all"]), ("status", ["-All"]), ("stop", ["-All"]), ("status", ["extra"]))
]


@pytest.mark.parametrize("shell, command, extra", [
    ("sh", "status", ["--all"]),
    ("sh", "stop", ["--all"]),
    ("sh", "status", ["extra"]),
    *_PS_OTHER_COMMANDS,
])
def test_options_on_other_commands_are_rejected(fake_project, shell, command, extra):
    result = run_clean(fake_project, None, extra, command=command, shell=shell)

    assert_exit(result, 1)
    assert f"[ERROR] '{command}' takes no options" in _lines(result), output(result)
    assert "not running" not in result.stdout, "the command itself must not run"
    assert "No running services found" not in result.stdout, "the command itself must not run"
    assert_artifacts_untouched(fake_project)


@pytest.mark.parametrize("shell", PS_SHELLS)
def test_ps1_all_without_a_command_prints_usage(fake_project, shell):
    env = clean_env(fake_project)
    data_dir = script_data_dir(env, "win32")
    seed_identity_files(data_dir)

    result = run_clean(fake_project, env, ["-All"], command=None, shell=shell)

    assert_exit(result, 1)
    lines = _lines(result)
    assert lines[0] == r"Usage: .\tools\sethlans.ps1 <command>", output(result)
    assert "[ERROR]" not in result.stdout
    assert "Sethlans Reborn -- Clean" not in result.stdout
    assert_artifacts_untouched(fake_project)
    assert_identity_files_kept(data_dir)
