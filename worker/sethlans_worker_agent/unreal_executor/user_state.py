# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Isolation of farm UE launches from the user's per-user Unreal files
(Unreal foundation FR-UE-27, D12; ``development/research/unreal-user-isolation.md``).

Mechanism: every UE child runs with ``USERPROFILE``, ``LOCALAPPDATA``,
``APPDATA``, ``HOMEDRIVE``/``HOMEPATH`` and ``TEMP``/``TMP`` pointed at one
persistent, owner-only **farm profile** ``P = CACHE_DIR\\ueprofile`` with its
folders pre-created (a missing folder makes ``SHGetKnownFolderPath`` fail
and UE fall back toward the drive root), plus ``-notraceserver``.

* ``isolation_env(P)`` / ``isolation_args()``: added by the launch builder
  after the FR-UE-18 scrub (``command.build_launch``), never taken from
  the worker's own environment. ``check_isolated_env`` is the runner's
  refusal (FR-UE-7).
* ``ensure_profile(P)``: creates the five folders (and their parents)
  owner-only, refusing a link or a foreign owner anywhere on the way.
* ``check_known_folders(P)``: the known-folder probe
  (``known_folder_probe``) in the isolation environment; Local AppData,
  Roaming AppData, Documents and the temp path must **equal** the farm
  profile's (``GetLongPathNameW``, ``normcase``, no trailing separator).
  A failed call, a 10 s timeout, unparseable output or another path fails.
* The node condition built on it (a failed check disables Unreal until
  the next passing check) is ``isolation_state``.

The farm-profile config check lives in ``profile_config`` and the
housekeeping in ``profile_housekeeping`` (re-exported here). NF-6: nothing
here runs while Unreal is disabled; callers check ``config_unreal.ENABLED``.
"""
import json
import logging
import os
import stat
import subprocess  # nosec B404 - list-form, fixed arguments
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from sethlans_worker_agent import env_cleanup
from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import known_folder_probe, winpath, zen_private
from sethlans_worker_agent.unreal_executor.profile_config import (  # noqa: F401 - re-exported
    ProfileConfigResult, scan_farm_profile_config)
from sethlans_worker_agent.unreal_executor.profile_housekeeping import housekeeping  # noqa: F401

logger = logging.getLogger(__name__)

PROFILE_DIRNAME = 'ueprofile'
PROFILE_FOLDERS: Tuple[Tuple[str, ...], ...] = (
    ('Profile', 'AppData', 'Local'),
    ('Profile', 'AppData', 'Roaming'),
    ('Profile', 'AppData', 'LocalLow'),
    ('Profile', 'Documents'),
    ('Temp',),
)
ISOLATION_VARS = env_cleanup.UNREAL_ISOLATION_VARS
NOTRACESERVER = '-notraceserver'
PROBE_ARG = '--sethlans-known-folder-probe'
PROBE_TIMEOUT_SECONDS = 10
PROBE_MAX_OUTPUT = 16 * 1024
SUMMARY = 'user_state=isolated'
REDIRECTED_REASON = ("Windows shell folders are redirected ({folder} resolves to {path}); farm Unreal "
                     "launches cannot be isolated. Use a dedicated render account.")
FOLDER_LABELS = {'local_appdata': 'Local AppData', 'roaming_appdata': 'Roaming AppData',
                 'documents': 'Documents', 'temp': 'the temp folder'}
_join = winpath.join


class IsolationError(ValueError):
    """A UE launch environment is not the FR-UE-27 isolation environment."""


def profile_dir(cache_dir) -> str:
    """``CACHE_DIR\\ueprofile`` as a string (wrap it in ``Path`` for file work)."""
    return _join(cache_dir, PROFILE_DIRNAME)


def isolation_env(profile) -> Dict[str, str]:
    """The seven redirected variables for farm profile ``profile`` (FR-UE-27)."""
    base = str(profile)
    user_profile = _join(base, 'Profile')
    drive, rest = winpath.module_for(base).splitdrive(user_profile)
    temp = _join(base, 'Temp')
    return {
        'USERPROFILE': user_profile,
        'LOCALAPPDATA': _join(base, 'Profile', 'AppData', 'Local'),
        'APPDATA': _join(base, 'Profile', 'AppData', 'Roaming'),
        'HOMEDRIVE': drive,
        'HOMEPATH': rest,
        'TEMP': temp,
        'TMP': temp,
    }


def isolation_args() -> Tuple[str, ...]:
    """Switches every farm UE launch carries for isolation (research §3.4)."""
    return (NOTRACESERVER,)


def check_isolated_env(env: Mapping[str, str], profile) -> None:
    """Raise ``IsolationError`` unless ``env`` carries exactly the isolation values."""
    expected = isolation_env(profile)
    upper = {key.upper(): value for key, value in env.items()}
    for name in ISOLATION_VARS:
        if name not in upper:
            raise IsolationError(f"refusing to start Unreal without {name} in its environment (FR-UE-27)")
        if upper[name] != expected[name]:
            raise IsolationError(f"refusing to start Unreal: {name} is not the farm profile (FR-UE-27)")


def profile_paths(profile) -> Tuple[Path, ...]:
    """``P`` and every folder of the farm profile, parents first."""
    paths = []
    for parts in PROFILE_FOLDERS:
        for depth in range(len(parts) + 1):
            path = Path(profile).joinpath(*parts[:depth])
            if path not in paths:
                paths.append(path)
    return tuple(paths)


def ensure_profile(profile) -> int:
    """Create every farm-profile folder owner-only (or check it). Returns how many were created.

    Raises ``OSError`` on a link, a non-directory or a foreign owner.
    """
    return sum(int(fs_safe.ensure_owned_dir(path)) for path in profile_paths(profile))


def _is_real_dir(path: Path) -> bool:
    try:
        st = fs_safe.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not fs_safe.is_reparse(st)


def missing_folders(profile) -> Tuple[str, ...]:
    """Farm-profile folders that are not real directories right now (a link counts as missing)."""
    return tuple('\\'.join(parts) for parts in PROFILE_FOLDERS
                 if not all(_is_real_dir(Path(profile).joinpath(*parts[:depth]))
                            for depth in range(len(parts) + 1)))


def repair_profile(profile, trash_dir) -> int:
    """``ensure_profile`` after moving any link or non-directory on a profile path to ``trash_dir``.

    Review S4: a junction planted at, say, ``Profile\\AppData\\Roaming`` or
    ``Temp`` would persistently send farm launches into another folder (the
    artist's). It is treated like farm-profile config contamination: the
    link itself is renamed into ``CACHE_DIR\\trash\\`` (never followed, its
    target is untouched) and the folder is re-created owner-only, so the node
    stays usable. A real directory with a foreign owner cannot be repaired
    safely and raises ``OSError`` (the caller releases the job). Returns how
    many entries were moved aside.
    """
    moved = 0
    for path in profile_paths(profile):
        if not fs_safe.lexists(path) or _is_real_dir(path):
            continue
        if fs_safe.move_aside(path, trash_dir) is None:
            raise OSError(f"could not move {path} (a link or not a folder) to trash")
        moved += 1
    if moved:
        logger.warning("Farm profile: moved %d link(s) or non-folder(s) to trash and re-created the folders.", moved)
    ensure_profile(profile)
    return moved


def expected_known_folders(profile) -> Dict[str, str]:
    return {
        'local_appdata': _join(profile, 'Profile', 'AppData', 'Local'),
        'roaming_appdata': _join(profile, 'Profile', 'AppData', 'Roaming'),
        'documents': _join(profile, 'Profile', 'Documents'),
        'temp': _join(profile, 'Temp'),
    }


@dataclass(frozen=True)
class KnownFolderResult:
    ok: bool
    reason: str = ''
    folder: str = ''
    path: str = ''
    checked_at: float = 0.0


def probe_command() -> list:
    """The probe child's argv: frozen worker + reserved argument, or ``python -I <file>``."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, PROBE_ARG]
    return [sys.executable, '-I', os.path.abspath(known_folder_probe.__file__)]


def _failure(folder: str, path: str, clock: Callable[[], float]) -> KnownFolderResult:
    label = FOLDER_LABELS.get(folder, folder)
    return KnownFolderResult(ok=False, folder=label, path=path,
                             reason=REDIRECTED_REASON.format(folder=label, path=path), checked_at=clock())


def evaluate_probe_output(stdout: str, profile, clock: Callable[[], float]) -> KnownFolderResult:
    """Compare the probe's JSON line with the farm profile."""
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
        folders, errors = data['folders'], data.get('errors') or {}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return _failure('local_appdata', 'unparseable probe output', clock)
    if not isinstance(folders, dict) or not isinstance(errors, dict):
        return _failure('local_appdata', 'unparseable probe output', clock)
    for name, expected in expected_known_folders(profile).items():
        got = folders.get(name)
        if not isinstance(got, str) or not got:
            return _failure(name, f"an error ({str(errors.get(name, 'no value'))[:40]})", clock)
        if zen_private.normalize_path(got) != zen_private.normalize_path(expected):
            return _failure(name, got[:260], clock)
    return KnownFolderResult(ok=True, checked_at=clock())


def check_known_folders(profile, base_env: Optional[Mapping[str, str]] = None,
                        run: Callable = subprocess.run, clock: Callable[[], float] = None,
                        command: Optional[Sequence[str]] = None) -> KnownFolderResult:
    """Run the probe in the isolation environment and compare (FR-UE-27). Never raises."""
    clock = clock or time.monotonic
    env = env_cleanup.clean_env_for_unreal(None, base_env=base_env)
    env.update(isolation_env(profile))
    argv = list(command) if command is not None else probe_command()
    try:
        completed = run(argv, env=env, cwd=str(profile), capture_output=True, text=True,
                        timeout=PROBE_TIMEOUT_SECONDS, creationflags=_no_window_flag())
    except subprocess.TimeoutExpired:
        return _failure('local_appdata', f'a probe timeout ({PROBE_TIMEOUT_SECONDS} s)', clock)
    except (OSError, ValueError) as exc:
        return _failure('local_appdata', f'a probe failure ({type(exc).__name__})', clock)
    return evaluate_probe_output((completed.stdout or '')[:PROBE_MAX_OUTPUT], profile, clock)


def _no_window_flag() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
