# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read the launcher's state for the tray (FR-LN-4, chunk 6c).

The launcher writes ``<settings>/setup/launcher_state.json``
(``launcher.setup_state.write_launcher_state``) on each state entry:
``{"state", "port", "reason"}``. The launcher is the source of truth;
the tray only shows what this file says.

* ``state``: ``SETUP``, ``STARTING``, ``RUNNING``, ``FAILED`` or
  ``STOPPING``. ``STARTING`` with no port is also what the launcher
  writes before setup mode answers.
* ``port``: the public port (setup page or dashboard), or ``None``.
* ``reason``: a one-line free-text reason, or ``None``. It is shown only
  in the tray's own menu and notifications, never sent anywhere.

:func:`read_launcher_state` never raises. A missing, unreadable,
oversized or garbled file, an unknown state, or anything but a regular
file this user owns (a link, a FIFO; ``settings_file_io.read_owned_file``)
gives
:data:`DEFAULT_STATE`: ``STARTING`` with no port and no reason, which
offers neither Copy Setup Token nor Reset.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared import settings_file
from shared.settings_file_io import read_owned_file

logger = logging.getLogger(__name__)

SETUP = "SETUP"
STARTING = "STARTING"
RUNNING = "RUNNING"
FAILED = "FAILED"
STOPPING = "STOPPING"
STATES = frozenset({SETUP, STARTING, RUNNING, FAILED, STOPPING})

STATE_FILE_NAME = "launcher_state.json"
MAX_STATE_BYTES = 4096
# The launcher caps a reason at 500 characters (setup_state.MAX_REASON_CHARS).
MAX_REASON_CHARS = 500


@dataclass(frozen=True)
class LauncherState:
    """What the launcher last wrote: its state, port and reason."""

    state: str
    port: Optional[int] = None
    reason: Optional[str] = None


DEFAULT_STATE = LauncherState(STARTING)


def state_file_path(settings_dir: Optional[Path] = None) -> Path:
    """``<settings>/setup/launcher_state.json``."""
    folder = settings_file.get_settings_dir() if settings_dir is None else Path(settings_dir)
    return folder / settings_file.SETUP_SUBDIR / STATE_FILE_NAME


def _port(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 65535 else None


def _reason(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    printable = "".join(ch if ch.isprintable() else " " for ch in value)
    cleaned = " ".join(printable.split())[:MAX_REASON_CHARS]
    return cleaned or None


def _parse(data: bytes) -> LauncherState:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("state") not in STATES:
        logger.warning("Launcher state file has no known state; using %s", DEFAULT_STATE.state)
        return DEFAULT_STATE
    return LauncherState(payload["state"], _port(payload.get("port")), _reason(payload.get("reason")))


def read_launcher_state(settings_dir: Optional[Path] = None) -> LauncherState:
    """Return the launcher's state (module doc); never raises."""
    try:
        path = state_file_path(settings_dir)
        data = read_owned_file(path, MAX_STATE_BYTES)
    except FileNotFoundError:
        return DEFAULT_STATE
    except Exception as exc:  # noqa: BLE001 - the tray must keep running
        logger.warning("Could not read the launcher state: %s", type(exc).__name__)
        return DEFAULT_STATE
    if len(data) > MAX_STATE_BYTES:
        logger.warning("Launcher state file is larger than %d bytes; ignored", MAX_STATE_BYTES)
        return DEFAULT_STATE
    try:
        return _parse(data)
    except Exception:  # noqa: BLE001 - bad UTF-8, bad JSON, too deeply nested
        logger.warning("Launcher state file is not valid JSON; using %s", DEFAULT_STATE.state)
        return DEFAULT_STATE
