# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode helpers for the bootstrap launcher.

Extracted from ``run_launcher.py`` to keep it under the 300-line limit.
These functions handle port detection, setup token management, and
atomic config writes.  No Django dependency — stdlib only.
"""

import configparser
import os
import secrets
import socket
import tempfile
from pathlib import Path

# Port range for auto-detection.
MANAGER_PORT = 8080
MANAGER_PORT_RANGE_END = 8099


def find_available_port(start: int = MANAGER_PORT) -> int:
    """Find an available port via trial ``socket.bind()``.

    Tries ports from *start* through ``MANAGER_PORT_RANGE_END``.
    Returns the first available port, or *start* if none are free
    (best-effort; uvicorn will handle the actual bind).
    """
    for port in range(start, MANAGER_PORT_RANGE_END + 1):
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            ) as sock:
                sock.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    return start


def generate_setup_token(manager_data: Path) -> str:
    """Generate a one-time setup token and write to manager.ini.

    The token is written to ``[setup] token`` in manager.ini.
    Returns the token string.
    """
    token = secrets.token_urlsafe(32)
    ini_path = manager_data / "manager.ini"

    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)

    if not config.has_section("setup"):
        config.add_section("setup")
    config.set("setup", "token", token)

    atomic_write_ini(config, ini_path)
    return token


def remove_setup_section(manager_data: Path) -> None:
    """Remove the ``[setup]`` section from manager.ini."""
    ini_path = manager_data / "manager.ini"
    if not ini_path.exists():
        return

    config = configparser.ConfigParser()
    config.read(ini_path)

    if config.has_section("setup"):
        config.remove_section("setup")
        atomic_write_ini(config, ini_path)


def atomic_write_ini(
    config: configparser.ConfigParser, path: Path,
) -> None:
    """Write a ConfigParser to *path* atomically."""
    parent = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".ini")
    try:
        with os.fdopen(fd, "w") as f:
            config.write(f)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
