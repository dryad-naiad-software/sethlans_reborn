# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-test environment for running the real ``clean`` command (issue #311).

``tools/sethlans.ps1 clean`` and ``tools/sethlans.sh clean`` also remove the
per-user Sethlans data folder, which they find from the environment:

- ``sethlans.ps1``: ``Join-Path $env:LOCALAPPDATA "Sethlans"``;
- ``sethlans.sh`` on macOS (``uname -s`` is ``Darwin``):
  ``$HOME/Library/Application Support/Sethlans``;
- ``sethlans.sh`` elsewhere: ``${XDG_DATA_HOME:-$HOME/.local/share}/sethlans``.

A child that inherits the developer's environment deletes their real identity
files (manager TLS key, secret key, worker ``config.json``). This module builds
a child environment with those variables pointed under a per-test folder and
refuses any environment whose target folder is not inside that folder.
"""

import os
import sys
from pathlib import Path


class UnsafeCleanEnvironment(AssertionError):
    """The child environment would let ``clean`` reach a real per-user folder."""


# Snapshot taken when the test package is imported, before any test can
# monkeypatch os.environ, so it reflects the environment pytest was started in.
_PARENT_ENV = dict(os.environ)


def script_data_dir(env, platform=sys.platform):
    """
    Return the per-user folder the clean script would delete under ``env``.

    Mirrors the scripts' own lookup. Returns ``None`` when the variable the
    script needs is missing or empty.
    """
    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        return Path(base) / "Sethlans" if base else None
    home = env.get("HOME", "")
    if platform == "darwin":
        return Path(f"{home}/Library/Application Support/Sethlans") if home else None
    # bash ${VAR:-default}: an empty XDG_DATA_HOME counts as unset.
    xdg = env.get("XDG_DATA_HOME") or (f"{home}/.local/share" if home else "")
    return Path(xdg) / "sethlans" if xdg else None


def _posix_account_home():
    """Home folder from the account database (ignores ``HOME``), or None."""
    try:
        import pwd
    except ImportError:
        return None
    return pwd.getpwuid(os.getuid()).pw_dir


def real_user_data_dirs(platform=sys.platform):
    """Per-user Sethlans folders that belong to the developer running pytest."""
    found = set()
    from_env = script_data_dir(_PARENT_ENV, platform)
    if from_env is not None:
        found.add(from_env)
    account_home = _posix_account_home() if platform != "win32" else None
    if account_home:
        found.add(script_data_dir({"HOME": account_home}, platform))
    return found


def isolated_env(base: Path, platform=sys.platform):
    """
    Return a copy of ``os.environ`` with every per-user folder variable the
    clean script reads pointed under ``base``. The folders are created.
    """
    env = dict(os.environ)
    if platform == "win32":
        local = base / "localappdata"
        local.mkdir(parents=True, exist_ok=True)
        env["LOCALAPPDATA"] = str(local)
        return env
    home = base / "home"
    xdg = base / "xdg-data"
    home.mkdir(parents=True, exist_ok=True)
    xdg.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(xdg)
    return env


def _key(path: Path):
    return os.path.normcase(str(path.resolve()))


def _overlaps(a: Path, b: Path):
    ka, kb = _key(a), _key(b)
    return ka == kb or ka.startswith(kb + os.sep) or kb.startswith(ka + os.sep)


def assert_env_isolated(env, base: Path, platform=sys.platform):
    """
    Fail loudly unless the clean script's per-user target under ``env`` lies
    inside ``base`` and does not overlap a real per-user Sethlans folder.
    """
    target = script_data_dir(env, platform)
    if target is None:
        raise UnsafeCleanEnvironment(
            "clean-script child environment has no per-user folder variable set; "
            "build it with isolated_env()"
        )
    for real in real_user_data_dirs(platform):
        if _overlaps(target, real):
            raise UnsafeCleanEnvironment(
                f"clean-script child would delete the real per-user folder {real}"
            )
    if not _key(target).startswith(_key(base) + os.sep):
        raise UnsafeCleanEnvironment(
            f"clean-script child target {target} is outside the per-test folder {base}"
        )
    return target
