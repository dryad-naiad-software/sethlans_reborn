# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #315: ``clean --all`` refuses a per-user folder it cannot name safely.

Each refusal exits 1, prints ``[ERROR] Refusing to remove the per-user data
folder: <reason>`` then ``Nothing was removed.``, and comes before any
deletion, so the repo artifacts are untouched too.

Where a refusal that did not happen could reach a folder outside the fake
project (a root-based path such as ``/.local/share/sethlans``), the test first
checks that folder does not exist and skips otherwise.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from . import _clean_runner as runner
from ._clean_asserts import assert_refused, seed_identity_files
from ._clean_runner import GIT_BASH_UNAME, PS_SHELLS, native_path, shown_path
from .conftest import IS_WINDOWS, SCRIPT_TIMEOUT, clean_env, run_clean


def require_absent(path: Path):
    """Skip unless ``path`` is absent, so a failed refusal cannot remove anything real."""
    if path.exists() or path.is_symlink():
        pytest.skip(f"{path} exists; a clean that failed to refuse could remove it")


def require_ps1_fallbacks_absent(project: Path):
    """
    Where sethlans.ps1 would have put the folder had it accepted a relative
    LOCALAPPDATA. ``relative\\dir`` resolves inside the fake project (the
    cwd). ``\\rooted`` resolves at the root of the project's drive.
    ``C:relative`` resolves against the child's current folder on C:, which
    is the fake project when it is on C:, else ``C:\\`` (the child gets no
    hidden ``=C:`` variable).
    """
    require_absent(Path(project.drive + "\\") / "rooted" / "Sethlans")
    if project.drive.upper() != "C:":
        require_absent(Path("C:\\") / "relative" / "Sethlans")


@pytest.mark.parametrize("value", ["", None], ids=["empty", "unset"])
class TestEmptyLocalAppData:
    @pytest.mark.parametrize("shell", PS_SHELLS)
    def test_ps1_refuses(self, fake_project, value, shell):
        env = clean_env(fake_project)
        if value is None:
            env.pop("LOCALAPPDATA")
        else:
            env["LOCALAPPDATA"] = value

        result = run_clean(fake_project, env, ["-All"], shell=shell, unsafe_values=True)

        assert_refused(result, fake_project, "LOCALAPPDATA is empty")

    def test_sh_git_bash_branch_refuses(self, fake_project, value):
        result = run_clean(fake_project, None, ["--all"], shell="sh", uname=GIT_BASH_UNAME,
                           inner_env={"LOCALAPPDATA": value}, unsafe_values=True)

        assert_refused(result, fake_project, "LOCALAPPDATA is empty")


@pytest.mark.skipif(not IS_WINDOWS, reason="drive-relative and rooted paths are Windows forms")
@pytest.mark.parametrize("shell", PS_SHELLS)
@pytest.mark.parametrize("value", [r"relative\dir", "C:relative", r"\rooted"])
def test_ps1_refuses_relative_localappdata(fake_project, value, shell):
    require_ps1_fallbacks_absent(Path(fake_project))
    env = clean_env(fake_project)
    env["LOCALAPPDATA"] = value

    result = run_clean(fake_project, env, ["-All"], shell=shell, unsafe_values=True)

    assert_refused(result, fake_project, f"LOCALAPPDATA is not an absolute path: '{value}'")


@pytest.mark.parametrize("value", [r"relative\dir", "C:relative", r"\rooted", "/rooted"])
def test_sh_git_bash_branch_refuses_relative_localappdata(fake_project, value):
    """
    The raw value is checked before cygpath, which would make ``\\rooted``
    into ``/<cwd drive>/rooted``. ``relative\\dir`` and ``C:relative`` stay
    relative after cygpath and would also fail the later ``/*`` check, so only
    the two rooted forms need a guard.
    """
    if IS_WINDOWS:
        require_absent(Path(Path(fake_project).drive + "\\") / "rooted" / "Sethlans")
        require_absent(native_path("/rooted/Sethlans"))
    env = clean_env(fake_project)
    env["LOCALAPPDATA"] = value

    result = run_clean(fake_project, env, ["--all"], shell="sh", uname=GIT_BASH_UNAME, unsafe_values=True)

    assert_refused(result, fake_project, f"LOCALAPPDATA is not an absolute path: '{value}'")


# resolve_user_data_dir() run on its own, with a fake uname (Git Bash branch)
# and a fake cygpath that only rewrites the string: nothing looks the path up,
# so no network share is touched.
_FAKE_CYGPATH = r"""#!/bin/sh
# cygpath -u PATH, string rewrite only: '\' -> '/', 'C:/x' -> '/c/x'.
printf '%s\n' "$2" | tr '\\' '/' | awk '{
    if ($0 ~ /^[A-Za-z]:\//) print "/" tolower(substr($0, 1, 1)) substr($0, 3); else print }'
"""
_RESOLVE_PROBE = (
    '{func}\nLOCALAPPDATA="$1"\nresolve_user_data_dir\n'
    'printf "dir=%s\\nerror=%s\\n" "$USER_DATA_DIR" "$USER_DATA_DIR_ERROR"\n'
)


def _resolve_in_isolation(fake_project, value):
    text = (fake_project / "tools" / "sethlans.sh").read_text()
    start = text.index("resolve_user_data_dir() {")
    func = text[start:text.index("\n}\n", start) + 2]
    runner.install_fake_uname(fake_project, GIT_BASH_UNAME)
    runner.write_tool(runner.fake_bin(fake_project) / "cygpath", _FAKE_CYGPATH)
    # A file, not ``bash -c``: the Windows command line would mangle the backslashes in the regex.
    probe = runner.fake_bin(fake_project) / "resolve-probe.sh"
    probe.write_bytes(_RESOLVE_PROBE.format(func=func).encode())
    env = clean_env(fake_project)
    env["PATH"] = runner.child_path("sh", fake_project, env)
    out = subprocess.run([runner.require_shell("sh"), str(probe), value],
                         cwd=str(fake_project), env=env, capture_output=True, text=True,
                         timeout=SCRIPT_TIMEOUT)
    assert out.returncode == 0, f"stdout:\n{out.stdout}\nstderr:\n{out.stderr}"
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


@pytest.mark.parametrize("value, expected", [
    ("\\\\server\\share\\x", "//server/share/x/Sethlans"),
    ("C:\\Users\\x\\AppData\\Local", "/c/Users/x/AppData/Local/Sethlans"),
])
def test_sh_git_bash_branch_accepts_unc_and_drive_paths(fake_project, value, expected):
    resolved = _resolve_in_isolation(fake_project, value)

    assert resolved == {"dir": expected, "error": ""}


def test_sh_resolver_probe_still_refuses_rooted(fake_project):
    """Control: the isolated resolver gives the same refusal as a full run."""
    resolved = _resolve_in_isolation(fake_project, "\\rooted")

    assert resolved == {"dir": "", "error": "LOCALAPPDATA is not an absolute path: '\\rooted'"}


class TestShLinuxBranchRefusals:
    def test_empty_home_with_xdg_unset(self, fake_project):
        require_absent(native_path("/.local/share/sethlans"))

        result = run_clean(fake_project, None, ["--all"], shell="sh", uname="Linux",
                           inner_env={"HOME": "", "XDG_DATA_HOME": None}, unsafe_values=True)

        assert_refused(result, fake_project, "HOME is empty and XDG_DATA_HOME is unset")

    def test_relative_xdg_data_home(self, fake_project):
        env = clean_env(fake_project)
        env["XDG_DATA_HOME"] = "relative/dir"
        home_dir = Path(env["HOME"]) / ".local" / "share" / "sethlans"
        seed_identity_files(home_dir)

        result = run_clean(fake_project, env, ["--all"], shell="sh", uname="Linux", unsafe_values=True)

        assert_refused(result, fake_project, "base folder is not an absolute path: 'relative/dir'")
        assert (home_dir / "manager" / "secret_key").exists(), "a refused XDG value must not fall back to HOME"


def test_sh_darwin_branch_refuses_empty_home(fake_project):
    require_absent(native_path("/Library/Application Support/Sethlans"))

    result = run_clean(fake_project, None, ["--all"], shell="sh", uname="Darwin",
                       inner_env={"HOME": ""}, unsafe_values=True)

    assert_refused(result, fake_project, "HOME is empty")


def _make_dir_link(link: Path, target: Path):
    """A junction on Windows, a directory symlink elsewhere."""
    if IS_WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_dir_link(link: Path):
    """Remove the link itself, never what it points at."""
    if IS_WINDOWS and os.path.isjunction(link):
        os.rmdir(link)
    elif link.is_symlink():
        link.unlink()


@pytest.fixture
def linked_data_dir(fake_project):
    """The per-user folder (own branch) made a link to a folder with identity files."""
    env = clean_env(fake_project)
    data_dir = Path(env["LOCALAPPDATA"] if IS_WINDOWS else env["XDG_DATA_HOME"])
    if sys.platform == "darwin":
        data_dir = Path(env["HOME"]) / "Library" / "Application Support"
        data_dir.mkdir(parents=True)
    data_dir = data_dir / ("Sethlans" if IS_WINDOWS or sys.platform == "darwin" else "sethlans")
    target = fake_project / "user-dirs" / "link-target"
    seed_identity_files(target)
    _make_dir_link(data_dir, target)
    try:
        yield env, data_dir, target
    finally:
        _remove_dir_link(data_dir)
        assert (target / "manager" / "secret_key").exists(), "removing the link must not follow it"


@pytest.mark.parametrize("shell, flag", [("ps1", "-All"), ("pwsh", "-All"), ("sh", "--all")])
def test_refuses_a_linked_user_folder(fake_project, linked_data_dir, shell, flag):
    env, data_dir, target = linked_data_dir

    result = run_clean(fake_project, env, [flag], shell=shell)

    shown = shown_path(shell, data_dir.parent, data_dir.name)
    assert_refused(result, fake_project, f"{shown} is a symbolic link or junction")
    assert (target / "manager" / "secret_key").read_text() == "fake secret"
    assert (target / "worker" / "config.json").exists()
    assert os.path.lexists(data_dir), "the link itself must be left in place"
