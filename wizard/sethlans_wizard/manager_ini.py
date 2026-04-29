# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single-helper updater for ``manager.ini`` (FR-M2-INI).

All `manager.ini` modifications go through :func:`update_manager_ini`
which serializes concurrent multi-section writes via the per-process
"manager-ini lock" defined at module scope. Without serialization, two
concurrent step handlers (e.g. network + database submitted in quick
succession) can each read the file before the other writes it back,
clobbering one section.

Atomic-write sequence per FR-PEND1a: temp file → fsync(temp_fd) →
close → ``os.replace`` → fsync(parent_dir_fd) on POSIX. ``chmod 600``
on POSIX, ``tighten_acls_windows`` on Windows.
"""

from __future__ import annotations

import configparser
import io
import logging
import os
import platform
import threading
from pathlib import Path
from typing import Mapping

from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

MANAGER_INI_FILENAME = "manager.ini"

# FR-M2-INI / concurrency-reviewer F5 — single per-process lock.
_manager_ini_lock: threading.Lock = threading.Lock()


def get_manager_ini_lock() -> threading.Lock:
    """Return the singleton manager-ini lock (FR-M2-INI)."""
    return _manager_ini_lock


def _stringify_values(values: Mapping[str, object]) -> dict[str, str]:
    """Coerce the value mapping to ``dict[str, str]`` for configparser."""
    out: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"INI key must be a non-empty str: {key!r}")
        if value is None:
            # Skip None — explicit deletes go through pop_option below.
            continue
        out[key] = str(value)
    return out


def update_manager_ini(
    data_dir: Path,
    section: str,
    values: Mapping[str, object],
) -> Path:
    """Atomically apply *values* to ``[section]`` in ``manager.ini``.

    Reads the existing file (if any), mutates the named section in
    place (existing keys outside *values* are preserved; existing keys
    in *values* are overwritten), and writes the result via the
    FR-PEND1a fsync sequence. Returns the path to the manager.ini.

    Multi-section writes are safe: callers acquire the same lock so
    no two writes can interleave their read-modify-write cycle.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(section, str) or not section:
        raise ValueError("section must be a non-empty str")
    target = data_dir / MANAGER_INI_FILENAME
    coerced = _stringify_values(values)
    with _manager_ini_lock:
        parser = configparser.ConfigParser()
        if target.exists():
            try:
                parser.read(str(target), encoding="utf-8")
            except configparser.Error as exc:
                logger.warning(
                    "Could not parse existing %s: %s; rewriting from scratch",
                    target,
                    exc,
                )
                parser = configparser.ConfigParser()
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in coerced.items():
            parser.set(section, key, value)
        buf = io.StringIO()
        parser.write(buf)
        body = buf.getvalue().encode("utf-8")
        _atomic_write(target, body)
    return target


def _atomic_write(target: Path, body: bytes) -> None:
    """FR-PEND1a fsync sequence + chmod 600 / Windows ACL tighten."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    if platform.system() != "Windows":
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
        except OSError as exc:
            logger.warning(
                "Could not open dir for fsync %s: %s", target.parent, exc,
            )
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        try:
            os.chmod(str(target), 0o600)
        except OSError as exc:
            logger.warning("Could not chmod %s: %s", target, exc)
    else:
        tighten_acls_windows(target)


__all__ = [
    "MANAGER_INI_FILENAME",
    "update_manager_ini",
    "get_manager_ini_lock",
]
