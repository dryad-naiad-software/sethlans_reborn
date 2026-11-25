# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures and helpers for tools/sethlans.{ps1,sh} clean script tests.

The fake_project fixture materializes a Sethlans-like layout under tmp_path
and copies both real scripts into it. The scripts resolve $PSScriptRoot /
$BASH_SOURCE from their own location, so once copied they automatically
treat tmp_path as the project root with no patching.

A plain ``clean`` keeps the per-user Sethlans folder it finds from
LOCALAPPDATA / HOME / XDG_DATA_HOME; ``clean --all`` (``-All``) removes it
(issue #315). Either way run_clean() never lets the child inherit those
variables (issue #311): every one of them points under the fake project,
and the environment is checked before anything is spawned; see
``_clean_env`` and ``_clean_runner``.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from . import _clean_runner as runner
from ._clean_env import (
    all_real_user_data_dirs,
    assert_env_isolated,
    assert_vars_safe,
    isolated_env,
    script_data_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_PS1 = REPO_ROOT / "tools" / "sethlans.ps1"
REAL_SH = REPO_ROOT / "tools" / "sethlans.sh"

# Make the ``caddy_fetch`` package importable by test modules under
# ``tests/unit/tools/``.  The production code does the same sys.path
# injection from ``tools/fetch_caddy.py`` and ``tools/dev_setup.py``.
_TOOLS_DIR = REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

IS_WINDOWS = sys.platform == "win32"
DEFAULT_SHELL = "ps1" if IS_WINDOWS else "sh"

# Generous timeout — first PowerShell invocation can be slow on cold caches.
SCRIPT_TIMEOUT = 60

_UNAME_PLATFORM = {"Linux": "linux", "Darwin": "darwin"}


@pytest.fixture
def fake_project(tmp_path):
    """
    Build a fake Sethlans project layout under tmp_path and copy both real
    scripts into tmp_path/tools/.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "manager").mkdir()
    (tmp_path / "manager" / "media" / "assets" / "test").mkdir(parents=True)
    (tmp_path / "manager" / "staticfiles").mkdir()
    (tmp_path / "manager" / "logs").mkdir()
    (tmp_path / "worker" / "sethlans_worker_agent" / "managed_tools").mkdir(parents=True)
    (tmp_path / "worker" / "sethlans_worker_agent" / "managed_assets").mkdir()
    (tmp_path / ".pids").mkdir()

    (tmp_path / "manager" / "db.sqlite3").write_bytes(b"fake db")
    (tmp_path / "manager" / "sethlans.ini").write_text("[server]\nport = 9443\n")
    (tmp_path / "manager" / "media" / "assets" / "test" / "foo.blend").write_bytes(b"fake blend")

    shutil.copy2(REAL_PS1, tmp_path / "tools" / "sethlans.ps1")
    shutil.copy2(REAL_SH, tmp_path / "tools" / "sethlans.sh")

    return tmp_path


def _top_level_entries(folder: Path):
    """Names directly inside ``folder``, or None when it does not exist."""
    if not folder.is_dir():
        return None
    return {child.name for child in folder.iterdir()}


@pytest.fixture(autouse=True)
def _real_user_data_dir_survives():
    """
    Fail loudly if a test in this package removes the developer's real
    per-user Sethlans folder or anything directly inside it (#311), under
    any of the scripts' lookups. Creating it (Django settings do on load)
    is allowed.
    """
    before = {folder: _top_level_entries(folder) for folder in all_real_user_data_dirs()}
    yield
    for folder, entries in before.items():
        if entries is None:
            continue
        after = _top_level_entries(folder)
        if after is None or not entries <= after:
            lost = sorted(entries - (after or set()))
            pytest.fail(f"real per-user folder {folder} was damaged; lost: {lost or 'the folder itself'}")


def user_dirs_base(project_root: Path) -> Path:
    """Per-test folder that holds the redirected per-user folders."""
    return project_root / "user-dirs"


def clean_env(project_root: Path) -> dict:
    """Child environment with the per-user folder variables under the fake project."""
    return isolated_env(user_dirs_base(project_root))


def _effective_env(env, inner_env):
    effective = dict(env)
    for name, value in (inner_env or {}).items():
        if value is None:
            effective.pop(name, None)
        else:
            effective[name] = value
    return effective


def _check_env(effective, base, shell, uname, unsafe_values):
    """Refuse, before anything is spawned, an env that could reach outside ``base``."""
    assert_vars_safe(effective, base, allow_unsafe_values=unsafe_values)
    platform = "win32" if shell in runner.PS_SHELLS else _UNAME_PLATFORM.get(uname, sys.platform)
    target = script_data_dir(effective, platform)
    if unsafe_values and (target is None or not target.is_absolute()):
        return
    assert_env_isolated(effective, base, platform)


def run_clean(project_root: Path, env=None, args=(), *, command="clean", shell=None,
              uname=None, inner_env=None, unsafe_values=False) -> subprocess.CompletedProcess:
    """
    Run ``command`` (default ``clean``; ``None`` for none) with extra ``args``
    (``--all``, ``-All``, ...) on one of the fake project's scripts.

    ``shell`` is "ps1" (Windows PowerShell), "pwsh" or "sh" (default: the
    platform's own script); the test is skipped when that shell is missing.
    sh runs always get a fake ``powershell.exe`` and ``taskkill`` first on
    PATH (``_clean_runner.install_fake_sweep``) unless the test installed
    its own. ``uname`` puts a fake ``uname``
    printing that value first on PATH (sh only). ``inner_env`` sets (or, with
    ``None``, unsets) variables inside bash, after Git Bash's start-up
    rewrite. ``unsafe_values`` allows a per-user variable to be unset, empty
    or relative, for tests where the script must refuse it.

    The child always gets a per-test environment (``clean_env``). An ``env``
    passed in is checked first and refused, before anything is spawned, if
    the script could reach a folder outside ``user_dirs_base(project_root)``.
    """
    shell = shell or DEFAULT_SHELL
    if env is None:
        env = clean_env(project_root)
    base = user_dirs_base(project_root)
    _check_env(_effective_env(env, inner_env), base, shell, uname, unsafe_values)
    argv = runner.build_command(shell, project_root, [command, *args] if command else list(args), inner_env)
    env = dict(env)
    if shell == "sh":
        if uname:
            runner.install_fake_uname(project_root, uname)
        if not (runner.fake_bin(project_root) / "taskkill").exists():
            runner.install_fake_sweep(project_root)
        if IS_WINDOWS:
            xdg = env.get("XDG_DATA_HOME")
            if xdg and Path(xdg).is_absolute() and "XDG_DATA_HOME" not in (inner_env or {}):
                env["XDG_DATA_HOME"] = runner.bash_path(xdg)
        env["PATH"] = runner.child_path(shell, project_root, env)
    return subprocess.run(
        argv,
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT,
    )
