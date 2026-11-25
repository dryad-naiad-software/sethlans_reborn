# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shells and fake tools for running the real clean scripts (issue #315).

``sethlans.ps1`` runs under Windows PowerShell ("ps1") and, when installed,
PowerShell 7 ("pwsh"). ``sethlans.sh`` ("sh") runs under bash; on Windows
that is Git Bash's ``usr/bin/bash.exe`` (never the WSL ``bash.exe`` in
System32), started directly so that it leaves ``PATH`` alone and the fake
tools put first on ``PATH`` win.

Git Bash rewrites some variables on start-up: an unset ``HOME`` becomes the
real profile folder and an empty one becomes ``<cwd>/=``. Variables that a
test needs unset or empty inside bash are therefore set by ``env`` running
inside bash (``inner_env``), after that rewrite.

The Git Bash process sweep lists processes with ``powershell.exe`` and kills
them with ``taskkill``. Every sh run gets a fake of both first on ``PATH``,
so no real process is ever listed or killed.
"""

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

IS_WINDOWS = sys.platform == "win32"
PS_SHELLS = ("ps1", "pwsh")
SHELLS = ("ps1", "pwsh", "sh")
# Any uname -s value matching the sh script's MINGW*|MSYS*|CYGWIN* branch.
GIT_BASH_UNAME = "MINGW64_NT-10.0"

# Ignores its arguments, prints fake-bin/ps-procs.txt ("Name\tPID\tCommandLine").
_FAKE_POWERSHELL = '#!/bin/sh\ncat "$(dirname "$0")/ps-procs.txt" 2>/dev/null\nexit 0\n'
_FAKE_TASKKILL = '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$(dirname "$0")/taskkill.log"\nexit 0\n'
# cygpath -m PATH for POSIX test runs: POSIX paths need no conversion.
_FAKE_CYGPATH = '#!/bin/sh\nprintf "%s\\n" "$2"\n'


def script_kind(shell):
    """"ps1" for both PowerShell shells, else "sh"."""
    return "ps1" if shell in PS_SHELLS else "sh"


def own_platform(shell):
    """The lookup the script uses on this machine: ps1 is always the Windows one."""
    return "win32" if shell in PS_SHELLS else sys.platform


@functools.cache
def git_bash():
    """Git Bash's ``usr/bin/bash.exe`` on Windows, ``bash`` elsewhere, or None."""
    if not IS_WINDOWS:
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).resolve().parents[1] / "usr" / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@functools.cache
def powershell(shell):
    """``powershell.exe`` / ``pwsh`` on Windows, or None (sethlans.ps1 targets Windows)."""
    if not IS_WINDOWS:
        return None
    return shutil.which("powershell.exe" if shell == "ps1" else "pwsh")


def require_shell(shell):
    """Path of the interpreter for ``shell``, or skip the test."""
    if shell in PS_SHELLS:
        exe = powershell(shell)
        if exe is None:
            pytest.skip(f"sethlans.ps1 needs {'Windows PowerShell' if shell == 'ps1' else 'pwsh'} on Windows")
        return exe
    exe = git_bash()
    if exe is None:
        pytest.skip("sethlans.sh needs bash (Git Bash on Windows)")
    return exe


def _bash_path_env():
    env = dict(os.environ)
    env["PATH"] = str(Path(git_bash()).parent) + os.pathsep + env.get("PATH", "")
    return env


def _cygpath(flag, value):
    out = subprocess.run(
        [git_bash(), "-c", f'cygpath {flag} "$1"', "cygpath", str(value)],
        env=_bash_path_env(), capture_output=True, text=True, timeout=30, check=True,
    )
    return out.stdout.strip()


@functools.cache
def bash_path(native) -> str:
    """``native`` as Git Bash shows it (``cygpath -u``); unchanged off Windows."""
    return _cygpath("-u", native) if IS_WINDOWS else str(native)


@functools.cache
def native_path(shown: str) -> Path:
    """Inverse of ``bash_path`` (``cygpath -w``); unchanged off Windows."""
    return Path(_cygpath("-w", shown)) if IS_WINDOWS else Path(shown)


def shown_path(shell, base, *parts) -> str:
    """How ``shell`` prints the folder ``base/parts...`` in its messages."""
    if shell in PS_SHELLS:
        return str(Path(base, *parts))
    return "/".join([bash_path(base), *parts])


def write_tool(path: Path, text: str):
    """An LF shell script, executable on POSIX too."""
    path.write_bytes(text.encode("utf-8"))
    path.chmod(0o755)


def fake_bin(project_root: Path) -> Path:
    """Folder for fake tools, first on the child's PATH."""
    folder = project_root / "fake-bin"
    folder.mkdir(exist_ok=True)
    return folder


def install_fake_uname(project_root: Path, system: str):
    """A ``uname`` that prints ``system`` (``uname -s``) to select a script branch."""
    write_tool(fake_bin(project_root) / "uname", f"#!/bin/sh\necho {system}\n")


def install_fake_cygpath(project_root: Path):
    """Off Windows only: a ``cygpath -m`` stand-in, so the Git Bash sweep can run."""
    if not IS_WINDOWS:
        write_tool(fake_bin(project_root) / "cygpath", _FAKE_CYGPATH)


def install_fake_sweep(project_root: Path, processes=b"", with_powershell=True):
    """
    Fake ``powershell.exe`` (prints ``processes``, the sweep's
    ``Name\\tPID\\tCommandLine`` lines) and ``taskkill`` (logs its arguments to
    ``fake-bin/taskkill.log``). Returns the log path.
    """
    folder = fake_bin(project_root)
    write_tool(folder / "taskkill", _FAKE_TASKKILL)
    if with_powershell:
        write_tool(folder / "powershell.exe", _FAKE_POWERSHELL)
        (folder / "ps-procs.txt").write_bytes(processes)
    return folder / "taskkill.log"


def build_command(shell, project_root: Path, args, inner_env):
    """argv for running ``args`` (command and options) on the fake project's script."""
    exe = require_shell(shell)
    if shell in PS_SHELLS:
        script = project_root / "tools" / "sethlans.ps1"
        return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    script = project_root / "tools" / "sethlans.sh"
    inner_env = inner_env or {}
    # env takes its -u options before any NAME=VALUE.
    env_args = [arg for name, value in inner_env.items() if value is None for arg in ("-u", name)]
    env_args += [f"{name}={value}" for name, value in inner_env.items() if value is not None]
    return [exe, "-c", 'exec env "$@"', "sethlans-test-env", *env_args, "bash", str(script), *args]


def child_path(shell, project_root: Path, env):
    """PATH for the child: fake tools first, then Git Bash's tools on Windows."""
    parts = [str(fake_bin(project_root))]
    if shell == "sh" and IS_WINDOWS:
        parts.append(str(Path(git_bash()).parent))
    return os.pathsep.join(parts + [env.get("PATH", "")])
