# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures and helpers for tools/sethlans.{ps1,sh} clean script tests.

The fake_project fixture materializes a Sethlans-like layout under tmp_path
and copies the real platform-appropriate clean script into it. The scripts
resolve $PSScriptRoot / $BASH_SOURCE from their own location, so once copied
they automatically treat tmp_path as the project root with no patching.

``clean`` also removes the per-user Sethlans folder it finds from
LOCALAPPDATA / HOME / XDG_DATA_HOME, so run_clean() never lets the child
inherit those variables (issue #311); see ``_clean_env``.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ._clean_env import assert_env_isolated, isolated_env, real_user_data_dirs


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

# Generous timeout — first PowerShell invocation can be slow on cold caches.
SCRIPT_TIMEOUT = 60


@pytest.fixture
def fake_project(tmp_path):
    """
    Build a fake Sethlans project layout under tmp_path and copy the
    real platform-appropriate clean script into tmp_path/tools/.
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

    if IS_WINDOWS:
        shutil.copy2(REAL_PS1, tmp_path / "tools" / "sethlans.ps1")
    else:
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
    per-user Sethlans folder or anything directly inside it (#311).
    Creating it (Django settings do on load) is allowed.
    """
    before = {folder: _top_level_entries(folder) for folder in real_user_data_dirs()}
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


def run_clean(project_root: Path, env=None) -> subprocess.CompletedProcess:
    """
    Invoke the clean command on the platform-appropriate fake script.

    The child always gets a per-test environment (``clean_env``). An ``env``
    passed in is checked first and refused, before anything is spawned, if
    the script would reach a folder outside ``user_dirs_base(project_root)``.
    """
    if env is None:
        env = clean_env(project_root)
    assert_env_isolated(env, user_dirs_base(project_root))
    if IS_WINDOWS:
        script = project_root / "tools" / "sethlans.ps1"
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "clean",
        ]
    else:
        script = project_root / "tools" / "sethlans.sh"
        cmd = ["bash", str(script), "clean"]

    return subprocess.run(
        cmd,
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT,
    )
