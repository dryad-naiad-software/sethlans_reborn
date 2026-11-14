# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pilot's CSV profiler capture (Unreal foundation FR-UE-25 "CSV
location", FR-UE-27 "Pilot CSV captures"; Dom R3-10).

The capture lives in the farm profile's
``P\\Profile\\AppData\\Local\\UnrealEngine\\5.8\\Saved\\Profiling\\CSV\\``.

**Location (chunk 7 integration requirement 14).** 5.8.4 logs the file
name only when a capture ends normally: ``CsvProfiler.cpp:4533``
``"Capture Ended. Writing CSV to file : %ls"``. A capture that runs until
UE exits (``-csvCaptureFrames=5000000``) is written during shutdown after
the log closed, so that line is absent from a pilot's render log (S6's
render logs never contain it). The file is therefore found by, in order:

1. the ``Writing CSV to file`` line when present (its path must lie in the
   farm CSV directory);
2. otherwise the files **new** in that directory since the render launched
   (a before/after listing of a directory only farm launches write, on a
   machine the pilot holds exclusively); exactly one new ``.csv`` file is
   accepted, more than one gives no CSV.

The chosen file must be a regular file (no reparse point), at most 256 MB,
inside that directory; it is moved into ``j/<...>/logs/``. Any failure
gives ``csv_local_used_peak_mb = None`` (the pilot still succeeds on the
card term). ``GPUMem/LocalUsedMB`` is parsed row by row; a truncated last
row is skipped; only finite values in ``[0, MAX_PEAK_MB]`` count. The folder
chain from ``P`` down is ``lstat``-checked (no link, all directories) before
it is listed or read.
"""
import csv
import logging
import math
import os
import re
import stat
from pathlib import Path
from typing import FrozenSet, Optional

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import winpath
from sethlans_worker_agent.unreal_executor.log_tail import iter_log_lines
from shared.unreal.vram_gate import MAX_PEAK_MB

logger = logging.getLogger(__name__)

MAX_CSV_BYTES = 256 * 1024 * 1024
COLUMN = 'GPUMem/LocalUsedMB'
WRITING_LINE = re.compile(r'LogCsvProfiler: (?:\w+: )?Capture Ended\. Writing CSV to file : (.{1,1024})$')
CSV_RELPATH = ('Profile', 'AppData', 'Local', 'UnrealEngine', '5.8', 'Saved', 'Profiling', 'CSV')


def csv_dir(profile) -> Path:
    return Path(profile).joinpath(*CSV_RELPATH)


def safe_csv_dir(profile) -> Optional[Path]:
    """The CSV folder, or None unless ``P`` and every folder down to it is a real directory (review W3).

    A junction anywhere on the chain (planted by project code running as the
    worker user) could otherwise make the worker read, or move, a file from
    another place; with it, there is simply no CSV value.
    """
    path = Path(profile)
    for part in ('',) + CSV_RELPATH:
        path = path / part if part else path
        try:
            st = fs_safe.lstat(path)
        except OSError:
            return None
        if fs_safe.is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            return None
    return path


def listing(profile) -> FrozenSet[str]:
    """Names in the farm CSV folder (empty when it is missing or unsafe)."""
    directory = safe_csv_dir(profile)
    if directory is None:
        return frozenset()
    try:
        return frozenset(entry.name for entry in os.scandir(fs_safe.long_path(directory)))
    except OSError:
        return frozenset()


def logged_path(log_path) -> Optional[str]:
    found = None
    try:
        for line in iter_log_lines(log_path):
            match = WRITING_LINE.search(line.rstrip('\r\n'))
            if match:
                found = match.group(1).strip()
    except OSError:
        return None
    return found


def _inside(path: str, directory: Path) -> bool:
    root = winpath.normcase(str(directory)).rstrip('\\/')
    candidate = winpath.normcase(winpath.normpath(path.replace('/', '\\') if winpath.is_windows_style(path)
                                                  else path))
    return candidate.startswith(root + ('\\' if winpath.is_windows_style(root) else '/'))


def locate(log_path, profile, before: FrozenSet[str]) -> Optional[Path]:
    directory = safe_csv_dir(profile)
    if directory is None:
        return None
    named = logged_path(log_path)
    if named:
        if not _inside(named, directory):
            logger.warning("The pilot CSV %s is outside the farm profile; ignored.", named[:300])
            return None
        return directory / Path(named.replace('\\', '/')).name
    new = sorted(name for name in listing(profile) - before if name.lower().endswith('.csv'))
    if len(new) != 1:
        if new:
            logger.warning("%d new CSV captures in %s; none is used.", len(new), directory)
        return None
    return directory / new[0]


def _regular(path: Path) -> bool:
    try:
        st = fs_safe.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not fs_safe.is_reparse(st) and st.st_size <= MAX_CSV_BYTES


def _value(row, column) -> Optional[float]:
    try:
        value = float(row[column])
    except (ValueError, IndexError, OverflowError):
        return None
    return value if math.isfinite(value) and 0 <= value <= MAX_PEAK_MB else None


def peak_local_used_mb(path) -> Optional[int]:
    """The largest finite ``GPUMem/LocalUsedMB`` in ``[0, MAX_PEAK_MB]``.

    The module-wide ``csv.field_size_limit`` is left alone (review L6): a field
    above the default 128 KiB raises ``csv.Error``, which ends the scan and
    keeps the peak of the rows read so far (UE's metadata footer is the only
    long row and comes last).
    """
    peak = None
    with open(fs_safe.long_path(path), newline='', encoding='utf-8', errors='replace') as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader, None)
            if not header or COLUMN not in header:
                return None
            column = header.index(COLUMN)
            for row in reader:
                value = _value(row, column) if len(row) >= len(header) - 2 else None
                if value is not None and (peak is None or value > peak):
                    peak = value
        except csv.Error as exc:
            logger.warning("Pilot CSV parsing stopped early: %s", exc)
    return None if peak is None else int(round(peak))


def collect(log_path, profile, before: FrozenSet[str], logs_dir) -> Optional[int]:
    """Locate, check, move and parse the capture; None on any failure. Never raises."""
    try:
        path = locate(log_path, profile, before)
        if path is None or not _regular(path):
            return None
        moved = Path(logs_dir) / f"pilot.{path.name}"
        os.replace(fs_safe.long_path(path), fs_safe.long_path(moved))
        return peak_local_used_mb(moved)
    except (OSError, csv.Error, ValueError, OverflowError) as exc:
        logger.warning("The pilot CSV capture could not be collected: %s", exc)
        return None
