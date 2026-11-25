# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-test environment for running the real ``clean`` command (issues #311, #315).

``tools/sethlans.ps1 clean -All`` and ``tools/sethlans.sh clean --all`` remove
the per-user Sethlans data folder; a plain ``clean`` keeps it and only says
where it is. Both scripts find the folder from the environment:

- ``sethlans.ps1``: ``$env:LOCALAPPDATA\\Sethlans``;
- ``sethlans.sh`` on Windows (``uname -s`` is MINGW / MSYS / CYGWIN):
  ``$(cygpath -u "$LOCALAPPDATA")/Sethlans``;
- ``sethlans.sh`` on macOS (``Darwin``): ``$HOME/Library/Application Support/Sethlans``;
- ``sethlans.sh`` elsewhere: ``${XDG_DATA_HOME:-$HOME/.local/share}/sethlans``.

A child that inherits the developer's environment can reach their real
identity files (manager TLS key, secret key, worker ``config.json``). This
module builds a child environment with every one of those variables pointed
under a per-test folder and refuses any environment that could reach anywhere
else.
"""

import os
import sys
from pathlib import Path


class UnsafeCleanEnvironment(AssertionError):
    """The child environment would let ``clean`` reach a real per-user folder."""


# Snapshot taken when the test package is imported, before any test can
# monkeypatch os.environ, so it reflects the environment pytest was started in.
_PARENT_ENV = dict(os.environ)

# Every variable either script reads to find the per-user folder.
USER_DIR_VARS = ("LOCALAPPDATA", "HOME", "XDG_DATA_HOME")
_PLATFORMS = ("win32", "darwin", "linux")


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
    # Git Bash sets HOME from USERPROFILE when HOME is unset, so on Windows
    # the macOS / XDG lookups can reach the profile folder too.
    profile = _PARENT_ENV.get("USERPROFILE") if platform != "win32" else None
    if profile:
        found.add(script_data_dir({"HOME": profile}, platform))
    return found


def all_real_user_data_dirs():
    """Real per-user folders under every lookup, whatever branch a script takes."""
    found = set()
    for platform in _PLATFORMS:
        found |= real_user_data_dirs(platform)
    return found


def isolated_env(base: Path, platform=sys.platform):
    """
    Return a copy of ``os.environ`` with every per-user folder variable either
    script reads (``LOCALAPPDATA``, ``HOME``, ``XDG_DATA_HOME``) pointed under
    ``base``. The folders are created. ``platform`` is kept for callers; all
    three are set on every platform, so a script branch faked with another
    ``uname`` still lands under ``base``.
    """
    del platform
    env = dict(os.environ)
    for name, folder in (("LOCALAPPDATA", "localappdata"), ("HOME", "home"), ("XDG_DATA_HOME", "xdg-data")):
        path = base / folder
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


def _key(path: Path):
    return os.path.normcase(str(path.resolve()))


def _overlaps(a: Path, b: Path):
    ka, kb = _key(a), _key(b)
    return ka == kb or ka.startswith(kb + os.sep) or kb.startswith(ka + os.sep)


def _inside(path: Path, base: Path):
    return _key(path).startswith(_key(base) + os.sep)


def _check_not_real(target: Path):
    for real in all_real_user_data_dirs():
        if _overlaps(target, real):
            raise UnsafeCleanEnvironment(
                f"clean-script child would delete the real per-user folder {real}"
            )


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
    _check_not_real(target)
    if not _inside(target, base):
        raise UnsafeCleanEnvironment(
            f"clean-script child target {target} is outside the per-test folder {base}"
        )
    return target


def assert_vars_safe(env, base: Path, allow_unsafe_values=False):
    """
    Fail loudly unless every per-user variable in ``env`` names a folder
    inside ``base``.

    With ``allow_unsafe_values`` (a refusal test), a variable may instead be
    unset, empty or not absolute, which the script must refuse; an absolute
    value outside ``base`` is still refused here.
    """
    for name in USER_DIR_VARS:
        value = env.get(name)
        if allow_unsafe_values and (not value or not os.path.isabs(value)):
            continue
        if not value or not _inside(Path(value), base):
            raise UnsafeCleanEnvironment(
                f"clean-script child {name}={value!r} is not inside the per-test folder {base}"
            )
        _check_not_real(Path(value))
