# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Farm-profile housekeeping (Unreal foundation FR-UE-27 "Housekeeping",
FR-PA-18 step 1b, NF-9).

At worker start (recovery step 1b, after the orphan sweep), ``housekeeping``:

* deletes everything under ``P\\Temp\\`` (the folder itself stays);
* trims ``P\\Profile\\AppData\\Local\\UnrealEngine\\5.8\\Saved\\Logs\\`` and
  ``...\\Saved\\crash-reports\\``: every entry beyond the newest
  ``KEEP_NEWEST`` (20), or older than ``MAX_AGE_DAYS`` (7), is deleted;
* never deletes the GPU driver shader caches (``AppData\\Local\\NVIDIA``,
  ``D3DSCache``, ``AMD``), which must persist (research §2).

Nothing is followed through a link: a link anywhere on the way to a
trimmed folder skips that folder, and links inside are removed as links
(``fs_safe.remove_tree``). Never raises.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe

logger = logging.getLogger(__name__)

KEEP_NEWEST = 20
MAX_AGE_DAYS = 7
ENGINE_USER_VERSION = '5.8'
TEMP_PARTS = ('Temp',)


def trimmed_dirs(version: str = ENGINE_USER_VERSION) -> Tuple[Tuple[str, ...], ...]:
    saved = ('Profile', 'AppData', 'Local', 'UnrealEngine', version, 'Saved')
    return (saved + ('Logs',), saved + ('crash-reports',))


@dataclass
class HousekeepingResult:
    temp_removed: int = 0
    trimmed: int = 0
    failed: int = 0
    skipped: List[str] = field(default_factory=list)


def _safe_dir(profile: Path, parts: Tuple[str, ...]) -> Optional[Path]:
    """``profile/parts`` when every component is a real directory (no link), else None."""
    current = profile
    for depth in range(len(parts) + 1):
        current = profile.joinpath(*parts[:depth])
        try:
            st = fs_safe.lstat(current)
        except OSError:
            return None
        if fs_safe.is_reparse(st) or not os.path.isdir(fs_safe.long_path(current)):
            return None
    return current


def _entries(directory: Path) -> List[Tuple[float, Path]]:
    found = []
    with os.scandir(fs_safe.long_path(directory)) as listing:
        names = [entry.name for entry in listing]
    for name in names:
        path = directory / name
        try:
            found.append((fs_safe.lstat(path).st_mtime, path))
        except OSError:
            continue
    return found


def _remove(path: Path, result: HousekeepingResult) -> bool:
    if fs_safe.remove_tree(path):
        return True
    result.failed += 1
    return False


def _clear_temp(profile: Path, result: HousekeepingResult) -> None:
    temp = _safe_dir(profile, TEMP_PARTS)
    if temp is None:
        if fs_safe.lexists(profile.joinpath(*TEMP_PARTS)):
            result.skipped.append('Temp')
        return
    for _mtime, path in _entries(temp):
        result.temp_removed += int(_remove(path, result))


def _trim(directory: Path, now: float, result: HousekeepingResult) -> None:
    entries = sorted(_entries(directory), key=lambda item: item[0], reverse=True)
    cutoff = now - MAX_AGE_DAYS * 86400
    for index, (mtime, path) in enumerate(entries):
        if index >= KEEP_NEWEST or mtime < cutoff:
            result.trimmed += int(_remove(path, result))


def housekeeping(profile, now: Optional[float] = None, clock: Callable[[], float] = time.time,
                 version: str = ENGINE_USER_VERSION) -> HousekeepingResult:
    """Trim the farm profile (FR-UE-27). Never raises."""
    profile = Path(profile)
    result = HousekeepingResult()
    now = clock() if now is None else now
    try:
        _clear_temp(profile, result)
        for parts in trimmed_dirs(version):
            directory = _safe_dir(profile, parts)
            if directory is None:
                if fs_safe.lexists(profile.joinpath(*parts)):
                    result.skipped.append('\\'.join(parts))
                continue
            _trim(directory, now, result)
    except OSError as exc:
        logger.warning("Farm profile housekeeping stopped early: %s", exc)
        result.failed += 1
    if result.skipped:
        logger.warning("Farm profile housekeeping skipped (link or not a folder): %s", ', '.join(result.skipped))
    return result
