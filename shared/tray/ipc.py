# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""File-based IPC from the tray to the launcher.

The tray writes one of three marker files to ``<data_dir>`` (the
settings folder):

* ``.restart_requested`` — launcher restarts the manager.
* ``.quit_requested`` — launcher terminates the manager only, or the
  entire application (target = ``"manager"`` or ``"all"``).
* ``.reset_requested`` — Reset setup (FR-RS-1), target ``"all"`` only.
  The launcher acts on it in ``RUNNING`` and ``FAILED`` only, and drops
  one made before it entered that state or more than 30 s old.

Each marker is written atomically via a tempfile in the same directory
followed by ``os.replace`` (cross-platform atomic rename).  The payload
carries an HMAC-validated secret, the tray PID, the action (which must
match the marker's file name), the target, and an ISO-8601 timestamp;
the launcher verifies all five on read (``launcher.tray_ipc``).

See tray spec FR-20 / FR-20a / FR-20b / FR-20c.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_SECRET = "SETHLANS_TRAY_IPC_SECRET"

MARKER_RESTART = ".restart_requested"
MARKER_QUIT = ".quit_requested"
MARKER_RESET = ".reset_requested"

# Marker file name -> the ``action`` the launcher expects in its payload,
# and the targets ``launcher.tray_ipc`` accepts for it.
_ACTIONS = {MARKER_RESTART: "restart", MARKER_QUIT: "quit", MARKER_RESET: "reset"}
_TARGETS = {
    MARKER_RESTART: frozenset({"manager", "all"}),
    MARKER_QUIT: frozenset({"manager", "all"}),
    MARKER_RESET: frozenset({"all"}),
}


def marker_exists(data_dir: Path, name: str) -> bool:
    """Return True if ``<data_dir>/<name>`` exists."""
    return (data_dir / name).exists()


def _get_secret() -> str:
    secret = os.environ.get(ENV_SECRET, "")
    if not secret:
        logger.warning(
            "%s is unset; writing marker without launcher-validated "
            "secret (dev mode)", ENV_SECRET,
        )
    return secret


def _write_marker(data_dir: Path, name: str, target: str) -> Path:
    """Atomically write a marker file with the canonical envelope."""
    if target not in _TARGETS[name]:
        raise ValueError(
            f"Invalid IPC target {target!r}; must be one of "
            f"{sorted(_TARGETS[name])}",
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "secret": _get_secret(),
        "pid": os.getpid(),
        "action": _ACTIONS[name],
        "target": target,
        "requested_at": _dt.datetime.now(
            _dt.timezone.utc,
        ).isoformat(),
    }
    body = json.dumps(payload).encode("utf-8")
    fd, tmp = tempfile.mkstemp(
        prefix=f"{name}.tmp.", dir=str(data_dir),
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        if sys.platform != "win32":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        final = data_dir / name
        os.replace(tmp, str(final))
        logger.info("Wrote IPC marker %s (target=%s)", final, target)
        return final
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def request_restart(data_dir: Path) -> Path:
    """Write ``.restart_requested`` with ``target="manager"``."""
    return _write_marker(data_dir, MARKER_RESTART, "manager")


def request_quit(data_dir: Path, target: str = "all") -> Path:
    """Write ``.quit_requested`` with the given target.

    *target* must be either ``"manager"`` (Quit Manager menu item) or
    ``"all"`` (Quit Sethlans menu item).
    """
    return _write_marker(data_dir, MARKER_QUIT, target)


def request_reset(data_dir: Path) -> Path:
    """Write ``.reset_requested`` (action ``reset``, target ``all``, a fresh
    ``requested_at``): Reset setup, after the person confirmed it."""
    return _write_marker(data_dir, MARKER_RESET, "all")
