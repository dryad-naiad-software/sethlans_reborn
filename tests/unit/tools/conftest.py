# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures and helpers for tools/sethlans.{ps1,sh} clean script tests.

The fake_project fixture materializes a Sethlans-like layout under tmp_path
and copies the real platform-appropriate clean script into it. The scripts
resolve $PSScriptRoot / $BASH_SOURCE from their own location, so once copied
they automatically treat tmp_path as the project root with no patching.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_PS1 = REPO_ROOT / "tools" / "sethlans.ps1"
REAL_SH = REPO_ROOT / "tools" / "sethlans.sh"

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
    (tmp_path / "manager" / "manager.ini").write_text("[server]\nport = 8080\n")
    (tmp_path / "manager" / "media" / "assets" / "test" / "foo.blend").write_bytes(b"fake blend")

    if IS_WINDOWS:
        shutil.copy2(REAL_PS1, tmp_path / "tools" / "sethlans.ps1")
    else:
        shutil.copy2(REAL_SH, tmp_path / "tools" / "sethlans.sh")

    return tmp_path


def run_clean(project_root: Path) -> subprocess.CompletedProcess:
    """Invoke the clean command on the platform-appropriate fake script."""
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
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT,
    )
