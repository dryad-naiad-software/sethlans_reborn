# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-side file-IPC helpers for the tray.

Implements the spec sections FR-20b / FR-20c / FR-20d / FR-20e /
FR-20f / FR-20g:

* Secret generation (``secrets.token_urlsafe(32)``), one per launcher
  session.
* Marker read + HMAC/PID/target/staleness validation.
* Stale-marker sweep on startup.
* Quit-beats-restart collision resolution.
* Optional Windows ACL tightening when ``pywin32`` is available
  (re-exported from ``shared.file_acls`` for backward compat).

Stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import hmac
import json
import logging
import secrets
from pathlib import Path
from typing import Optional

# Re-exported for backward compatibility with code that imported the
# helper from this module before it moved to ``shared.file_acls``.
from shared.file_acls import tighten_acls_windows  # noqa: F401

logger = logging.getLogger(__name__)

MARKER_RESTART = ".restart_requested"
MARKER_QUIT = ".quit_requested"

_VALID_TARGETS = frozenset({"manager", "all"})
_MARKER_MAX_BYTES = 4096
_STALE_SECONDS = 30.0


def generate_secret() -> str:
    """Generate a fresh per-session IPC secret."""
    return secrets.token_urlsafe(32)


def sweep_stale_markers(data_dir: Path) -> None:
    """Delete residue markers from a prior crash (FR-20e)."""
    for name in (MARKER_RESTART, MARKER_QUIT):
        p = data_dir / name
        try:
            p.unlink()
            logger.info("Swept stale marker %s", p)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not sweep %s: %s", p, exc)


def _delete_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)


def _parse_marker(raw: str) -> Optional[dict]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _is_stale(payload: dict) -> bool:
    ts = payload.get("requested_at")
    if not isinstance(ts, str):
        return False
    try:
        requested = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return True
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=_dt.timezone.utc)
    age = (_dt.datetime.now(_dt.timezone.utc) - requested).total_seconds()
    return age > _STALE_SECONDS


def _validate_payload(
    path: Path, payload: dict,
    expected_secret: str, expected_tray_pid: int,
) -> Optional[str]:
    # Empty-secret guard: refuse to validate markers when the
    # launcher has no usable secret yet.  hmac.compare_digest("", "")
    # returns True, which would otherwise accept any marker that
    # leaves ``secret`` empty.
    if not expected_secret or len(expected_secret) < 32:
        logger.warning(
            "Launcher IPC secret unset or too short; refusing marker %s",
            path,
        )
        return None
    secret = str(payload.get("secret", ""))
    target = str(payload.get("target", ""))
    if not hmac.compare_digest(secret, expected_secret):
        logger.warning("Marker %s failed secret HMAC check", path)
        return None
    try:
        pid_int = int(payload.get("pid"))
    except (TypeError, ValueError):
        logger.warning("Marker %s has bad pid", path)
        return None
    if pid_int != expected_tray_pid:
        logger.warning(
            "Marker %s pid=%d != expected=%d",
            path, pid_int, expected_tray_pid,
        )
        return None
    if target not in _VALID_TARGETS:
        logger.warning("Marker %s target=%r not allowed", path, target)
        return None
    if _is_stale(payload):
        logger.info("Marker %s is stale; ignored", path)
        return None
    return target


def read_and_validate_marker(
    path: Path, expected_secret: str, expected_tray_pid: int,
) -> Optional[str]:
    """Return the validated target or None.

    Always deletes the marker file before returning.  Any validation
    failure logs a WARNING and returns None.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        _delete_quiet(path)
        return None
    _delete_quiet(path)
    if len(raw) > _MARKER_MAX_BYTES:
        logger.warning("Marker %s exceeds size cap", path)
        return None
    payload = _parse_marker(raw)
    if payload is None:
        logger.warning("Marker %s has malformed JSON", path)
        return None
    return _validate_payload(
        path, payload, expected_secret, expected_tray_pid,
    )


def consume_pending_ipc(
    data_dir: Path, expected_secret: str, expected_tray_pid: int,
) -> tuple[Optional[str], Optional[str]]:
    """Read + validate + delete any pending markers.

    Returns ``(quit_target, restart_target)``.  If both markers exist,
    quit wins (FR-20f) and the restart marker is deleted unread.
    """
    quit_path = data_dir / MARKER_QUIT
    restart_path = data_dir / MARKER_RESTART

    if quit_path.exists() and restart_path.exists():
        logger.warning(
            "Both .quit_requested and .restart_requested present; "
            "quit wins, discarding restart",
        )
        _delete_quiet(restart_path)

    quit_target = None
    if quit_path.exists():
        quit_target = read_and_validate_marker(
            quit_path, expected_secret, expected_tray_pid,
        )

    restart_target = None
    if restart_path.exists() and quit_target is None:
        restart_target = read_and_validate_marker(
            restart_path, expected_secret, expected_tray_pid,
        )

    return quit_target, restart_target
