# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Atomic ``manager.ini`` helpers.

All ``manager.ini`` writes during setup must go through this module so
ordering (tempfile + ``os.replace``) remains consistent.
"""

from __future__ import annotations

import configparser
import logging
import os
import platform
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _write_parser_atomic(parser: configparser.ConfigParser, ini_path: Path) -> None:
    ini_path = Path(ini_path)
    parent = str(ini_path.parent)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".ini")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            parser.write(f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, str(ini_path))
        if platform.system() != "Windows":
            try:
                os.chmod(str(ini_path), 0o600)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_ini_atomic(ini_path: Path, updates: dict) -> None:
    """Merge ``updates`` into ``ini_path`` atomically.

    ``updates`` keys use dot notation (``"section.option"``). Creates
    missing sections.  Non-string values are stringified.
    """
    ini_path = Path(ini_path)
    parser = configparser.ConfigParser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")
    for dotted_key, value in updates.items():
        section, _, option = dotted_key.partition(".")
        if not option:
            raise ValueError(
                f"update key {dotted_key!r} must be 'section.option'"
            )
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, option, str(value))
    _write_parser_atomic(parser, ini_path)


def remove_ini_section(ini_path: Path, section: str) -> None:
    """Atomically remove ``section`` from ``ini_path`` if present."""
    ini_path = Path(ini_path)
    if not ini_path.exists():
        return
    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")
    if not parser.has_section(section):
        return
    parser.remove_section(section)
    _write_parser_atomic(parser, ini_path)


def read_setup_session_id(data_dir: Path) -> Optional[str]:
    """Return ``[setup] session_id`` from ``manager.ini`` or None."""
    ini_path = Path(data_dir) / "manager.ini"
    if not ini_path.exists():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
    except configparser.Error:
        return None
    return parser.get("setup", "session_id", fallback=None) or None


def bind_setup_session_id(data_dir: Path, session_id: str) -> bool:
    """Bind ``session_id`` to ``[setup]`` if unset.

    Returns ``True`` if the caller became the bound session (either
    unset before or already equal), ``False`` if a *different*
    session_id is already bound.
    """
    ini_path = Path(data_dir) / "manager.ini"
    current = read_setup_session_id(data_dir)
    if current and current != session_id:
        return False
    if current == session_id:
        return True
    write_ini_atomic(ini_path, {"setup.session_id": session_id})
    return True
