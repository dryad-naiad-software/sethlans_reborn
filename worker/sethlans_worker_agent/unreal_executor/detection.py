# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine install detection (Unreal foundation FR-WD-1..5).

Candidates come from the Epic launcher's ``LauncherInstalled.dat``
(``WindowsLauncherLocator``) and from ``SETHLANS_UNREAL_ENGINE_PATHS``
(``ConfiguredPathLocator``). Every candidate is rejected **before** any
``realpath``/``stat``/``exists`` call unless it is an absolute path on a
local drive letter whose drive is a fixed disk (``GetDriveTypeW``, so a
mapped network drive such as ``V:\\`` is refused too): an edited
``LauncherInstalled.dat`` must not be able to make the worker open
``\\\\attacker\\share`` and send NTLM credentials. ``LauncherInstalled.dat``
is read only up to 1 MiB. Rejected ``ENGINE_PATHS`` entries are logged
at WARNING once each; rejected launcher entries at DEBUG.

A candidate is an engine only if ``Engine/Build/Build.version`` and the
platform editor binary exist. Identity comes from ``Build.version``,
never from the launcher's ``AppName``/``AppVersion`` (plugin and
marketplace entries share the engine's ``InstallLocation`` and carry
unrelated versions). Launcher installs outside Program Files are
untrusted unless listed in ``ENGINE_PATHS``.

Only Windows is implemented (phase 1); elsewhere ``detect_engines()``
returns ``[]`` and logs once.
"""
import json
import logging
import os
import platform
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Tuple

from shared.unreal.builds import is_supported, parse_build

logger = logging.getLogger(__name__)

PLATFORM_LAYOUT = {'Windows': 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'}
BUILD_VERSION_RELPATH = 'Engine/Build/Build.version'
LAUNCHER_DAT_RELPATH = ('Epic', 'UnrealEngineLauncher', 'LauncherInstalled.dat')
UNTRUSTED_LOCATION_REASON = "install outside Program Files; add it to SETHLANS_UNREAL_ENGINE_PATHS to trust it"
_LOCAL_DRIVE = re.compile(r'^[A-Za-z]:\\', re.ASCII)
MAX_LAUNCHER_DAT_BYTES = 1024 * 1024
_DRIVE_FIXED = 3  # GetDriveTypeW: DRIVE_FIXED
_BUILD_KEYS = ('MajorVersion', 'MinorVersion', 'PatchVersion', 'Changelist')

_unsupported_os_logged = set()
_config_rejections_logged = set()
_log_lock = threading.Lock()  # leaf lock


@dataclass(frozen=True)
class EngineInstall:
    root: Path
    editor_cmd: Path
    build: Optional[str]
    supported: bool
    reason: str
    source: str  # 'launcher' | 'config'


class EngineLocator(Protocol):
    source: str

    def candidates(self) -> List[str]:
        ...


def is_local_drive_path(path: str) -> bool:
    """Absolute path on a local drive letter (``X:\\...``); purely lexical."""
    return bool(_LOCAL_DRIVE.match(path))


def is_fixed_drive(path: str) -> bool:
    """True when the drive of ``X:\\...`` is a fixed disk (``GetDriveTypeW``).

    Mapped network drives (``V:\\``), removable and RAM disks are refused
    before any ``realpath``/``stat``; ``GetDriveTypeW`` on a drive root makes
    no network call. False off Windows.
    """
    if sys.platform != 'win32' or not is_local_drive_path(path):
        return False
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(path[:3]) == _DRIVE_FIXED


def _reject(candidate: str, source: str, why: str) -> None:
    """Config rejections are logged at WARNING once each; launcher ones at DEBUG."""
    if source != 'config':
        logger.debug("Ignoring Unreal candidate %r (%s): %s.", candidate, source, why)
        return
    with _log_lock:
        if candidate in _config_rejections_logged:
            return
        _config_rejections_logged.add(candidate)
    logger.warning("Ignoring SETHLANS_UNREAL_ENGINE_PATHS entry %r: %s.", candidate, why)


class WindowsLauncherLocator:
    """``%PROGRAMDATA%\\Epic\\UnrealEngineLauncher\\LauncherInstalled.dat``."""
    source = 'launcher'

    def __init__(self, dat_path: Optional[str] = None):
        if dat_path is None:
            program_data = os.environ.get('PROGRAMDATA', '')
            dat_path = os.path.join(program_data, *LAUNCHER_DAT_RELPATH) if program_data else ''
        self.dat_path = dat_path

    def candidates(self) -> List[str]:
        if not self.dat_path or not is_fixed_drive(self.dat_path.replace('/', '\\')):
            return []
        try:
            # Bounded read (not stat-then-read): the file may grow in between.
            with open(self.dat_path, 'rb') as handle:
                raw = handle.read(MAX_LAUNCHER_DAT_BYTES + 1)
            if len(raw) > MAX_LAUNCHER_DAT_BYTES:
                logger.warning("Ignoring %s: it is over the %d byte limit.",
                               self.dat_path, MAX_LAUNCHER_DAT_BYTES)
                return []
            data = json.loads(raw.decode('utf-8-sig'))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s: %s", self.dat_path, exc)
            return []
        entries = data.get('InstallationList') if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return []
        return [entry['InstallLocation'] for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get('InstallLocation'), str)]


class ConfiguredPathLocator:
    """``SETHLANS_UNREAL_ENGINE_PATHS`` (``os.pathsep``-separated)."""
    source = 'config'

    def __init__(self, raw: str):
        self.raw = raw or ''

    def candidates(self) -> List[str]:
        return [part.strip() for part in self.raw.split(os.pathsep) if part.strip()]


def _program_files_roots() -> List[str]:
    roots = []
    for var in ('ProgramFiles', 'ProgramW6432'):
        value = os.environ.get(var, '')
        if value and is_local_drive_path(value):
            roots.append(os.path.normcase(os.path.realpath(value)))
    return roots


def _is_under(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip('\\/') + os.sep) for root in roots)


def read_build_version(path: Path) -> Tuple[Optional[str], str]:
    """``(build, reason)`` from ``Build.version``; reason is '' when usable."""
    try:
        with open(path, encoding='utf-8-sig') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, f"Build.version unreadable ({type(exc).__name__})"
    if not isinstance(data, dict):
        return None, "Build.version is not a JSON object"
    values = [data.get(key) for key in _BUILD_KEYS]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        return None, "Build.version has missing or invalid version numbers"
    build = "{}.{}.{}-{}".format(*values)
    if values[3] == 0:
        return build, "source build (Changelist 0) not supported"
    try:
        parse_build(build)
    except ValueError as exc:
        return None, f"Build.version invalid ({exc})"
    return build, ''


def _support(build: Optional[str], reason: str, trusted: bool) -> Tuple[bool, str]:
    if build is None or reason:
        return False, reason
    if not is_supported(build):
        major, minor = build.split('.')[:2]
        return False, f"UE {major}.{minor} not supported (R14)"
    if not trusted:
        return False, UNTRUSTED_LOCATION_REASON
    return True, ''


def _examine(candidate: str, source: str, binary: str, pf_roots: List[str]) -> Optional[EngineInstall]:
    """Turn one candidate into an install, or None if it is not an engine."""
    lexical = candidate.replace('/', '\\')
    if not is_local_drive_path(lexical):
        _reject(candidate, source, "not an absolute path on a local drive")
        return None
    if not is_fixed_drive(lexical):
        _reject(candidate, source, "not on a fixed local disk")
        return None
    resolved = os.path.realpath(lexical)
    if not is_local_drive_path(resolved) or not is_fixed_drive(resolved):
        _reject(candidate, source, "resolves off the fixed local disks")
        return None
    root = Path(resolved)
    editor_cmd = root / binary
    build_file = root / BUILD_VERSION_RELPATH
    if not (build_file.is_file() and editor_cmd.is_file()):
        _reject(candidate, source, "no Engine/Build/Build.version or editor binary")
        return None
    build, reason = read_build_version(build_file)
    trusted = source == 'config' or _is_under(os.path.normcase(resolved), pf_roots)
    supported, reason = _support(build, reason, trusted)
    return EngineInstall(root=root, editor_cmd=editor_cmd, build=build,
                         supported=supported, reason=reason, source=source)


def _log_unsupported_os_once(system: str) -> None:
    with _log_lock:
        if system in _unsupported_os_logged:
            return
        _unsupported_os_logged.add(system)
    logger.info("Unreal detection not implemented on %s (phase 4)", system)


def detect_engines(locators: Optional[List[EngineLocator]] = None,
                   system: Optional[str] = None,
                   engine_paths: str = '') -> List[EngineInstall]:
    """Detected installs, deduped by resolved path and sorted by path.

    Configured paths are examined first, so a root listed both in
    ``ENGINE_PATHS`` and by the launcher is reported as ``config``
    (trusted by the admin).
    """
    system = system or platform.system()
    binary = PLATFORM_LAYOUT.get(system)
    if binary is None:
        _log_unsupported_os_once(system)
        return []
    if locators is None:
        locators = [ConfiguredPathLocator(engine_paths), WindowsLauncherLocator()]
    pf_roots = _program_files_roots()
    found = {}
    for locator in locators:
        for candidate in locator.candidates():
            try:
                install = _examine(candidate, locator.source, binary, pf_roots)
            except (OSError, ValueError) as exc:
                logger.debug("Ignoring Unreal candidate %r: %s", candidate, exc)
                continue
            if install is not None:
                found.setdefault(os.path.normcase(str(install.root)), install)
    return [found[key] for key in sorted(found)]
