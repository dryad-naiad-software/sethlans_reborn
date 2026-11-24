# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single-helper updater for the wizard's settings-file sections (FR-M2-INI).

All wizard writes of ``[server]`` / ``[database]`` go through
:func:`update_manager_ini`, which writes the Sethlans settings file
(:func:`shared.settings_file.settings_path`, ``sethlans.ini``) via
:func:`shared.settings_file.set_values`: a cross-process lock around the
read-modify-write, then a whole-file atomic, owner-only replace. The
per-process "manager-ini lock" below is kept as well, so the verify
handler's read (which takes the same lock) never interleaves with an
in-process write.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Mapping, Optional

from shared import settings_file

logger = logging.getLogger(__name__)

MANAGER_INI_FILENAME = settings_file.SETTINGS_FILENAME

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
            # Skip None — a None value is never written as "None".
            continue
        out[key] = str(value)
    return out


def update_manager_ini(
    section: str,
    values: Mapping[str, object],
    ini_path: Optional[Path] = None,
) -> Path:
    """Atomically apply *values* to ``[section]`` of the settings file.

    *ini_path* defaults to :func:`shared.settings_file.settings_path`.
    Existing keys outside *values* are preserved; keys in *values* are
    overwritten; ``None`` values are skipped. Returns the path written.
    Raises ``OSError`` when the file cannot be read, parsed or written.
    """
    if not isinstance(section, str) or not section:
        raise ValueError("section must be a non-empty str")
    coerced = _stringify_values(values)
    target = settings_file.settings_path() if ini_path is None else Path(ini_path)
    with _manager_ini_lock:
        settings_file.set_values(section, coerced, target)
    return target


__all__ = [
    "MANAGER_INI_FILENAME",
    "update_manager_ini",
    "get_manager_ini_lock",
]
