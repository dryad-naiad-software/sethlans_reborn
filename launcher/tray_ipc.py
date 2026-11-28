# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-side file-IPC helpers for the tray.

Implements the spec sections FR-20b / FR-20c / FR-20d / FR-20e /
FR-20f / FR-20g:

* Secret generation (``secrets.token_urlsafe(32)``), one per launcher
  session.
* Marker read + HMAC/PID/action/target/staleness validation. The
  payload's ``action`` must match the marker's file name, so a quit or
  restart payload renamed to ``.reset_requested`` is refused; each action
  has its own targets. A reset must carry ``requested_at`` no more than
  30 s from now either way. At most 4 KiB is read.
* Stale-marker sweep on startup.
* The three-marker read of the running loop (:func:`consume_all_markers`;
  ``launcher.loop_decisions.ipc_precedence`` picks quit, then reset,
  then restart).
* A quit-only read for the IPC poll thread (:func:`consume_quit_marker`):
  it leaves the restart and reset markers for the main loop (#345).
* Each marker is claimed by one reader only: it is renamed to a unique
  ``<name>.<random>.claimed`` before it is read, so the poll thread and
  the loop never both act on one click.
* ``not_before``: the loop drops reset and restart requests made before
  it entered the state that reads them (a Reset clicked during SETUP or
  STARTING does not fire later).
* Optional Windows ACL tightening when ``pywin32`` is available
  (re-exported from ``shared.file_acls`` for backward compat).

Stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

# Re-exported for backward compatibility with code that imported the
# helper from this module before it moved to ``shared.file_acls``.
from shared.file_acls import tighten_acls_windows  # noqa: F401
from shared.settings_file_io import read_limited

logger = logging.getLogger(__name__)

MARKER_RESTART = ".restart_requested"
MARKER_QUIT = ".quit_requested"
MARKER_RESET = ".reset_requested"

ACTION_QUIT = "quit"
ACTION_RESTART = "restart"
ACTION_RESET = "reset"
# Marker file name -> the action its payload must name.
_ACTIONS = {MARKER_QUIT: ACTION_QUIT, MARKER_RESTART: ACTION_RESTART, MARKER_RESET: ACTION_RESET}
_TARGETS = {
    ACTION_QUIT: frozenset({"manager", "all"}),
    ACTION_RESTART: frozenset({"manager", "all"}),
    ACTION_RESET: frozenset({"all"}),
}
CLAIMED_SUFFIX = ".claimed"
_MARKER_MAX_BYTES = 4096
_STALE_SECONDS = 30.0


def generate_secret() -> str:
    """Generate a fresh per-session IPC secret."""
    return secrets.token_urlsafe(32)


def sweep_stale_markers(data_dir: Path) -> None:
    """Delete residue markers from a prior crash (FR-20e)."""
    for name in (MARKER_RESTART, MARKER_QUIT, MARKER_RESET):
        for p in [data_dir / name, *Path(data_dir).glob(f"{name}.*{CLAIMED_SUFFIX}")]:
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


def _is_stale(payload: dict, *, strict: bool = False) -> bool:
    """Older than 30 s. ``strict`` (reset): a missing time, or one more
    than 30 s in the future, is stale too."""
    ts = payload.get("requested_at")
    if not isinstance(ts, str):
        return strict
    try:
        requested = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return True
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=_dt.timezone.utc)
    age = (_dt.datetime.now(_dt.timezone.utc) - requested).total_seconds()
    return abs(age) > _STALE_SECONDS if strict else age > _STALE_SECONDS


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
    action = _ACTIONS.get(path.name)
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
    if action is None or payload.get("action") != action:
        logger.warning("Marker %s does not name its own action", path)
        return None
    if target not in _TARGETS[action]:
        logger.warning("Marker %s target=%r not allowed", path, target)
        return None
    if _is_stale(payload, strict=action == ACTION_RESET):
        logger.info("Marker %s is stale; ignored", path)
        return None
    return target


def _claim(path: Path) -> Optional[Path]:
    """Rename ``path`` to a name only this reader knows; ``None`` when another got it."""
    claimed = path.with_name(f"{path.name}.{secrets.token_hex(8)}{CLAIMED_SUFFIX}")
    try:
        os.replace(path, claimed)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not claim %s: %s", path, exc)
        return None
    return claimed


def _requested_before(payload: dict, not_before: _dt.datetime) -> bool:
    """True when the payload's time is before ``not_before`` (or unreadable)."""
    try:
        requested = _dt.datetime.fromisoformat(str(payload.get("requested_at")))
    except ValueError:
        return True
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=_dt.timezone.utc)
    return requested < not_before


def read_and_validate_marker(
    path: Path, expected_secret: str, expected_tray_pid: int,
    not_before: Optional[_dt.datetime] = None,
) -> Optional[str]:
    """Return the validated target or None.

    The marker is claimed (renamed) first and always deleted before
    returning. Any validation failure logs a WARNING and returns None; a
    request made before ``not_before`` is dropped.
    """
    claimed = _claim(path)
    if claimed is None:
        return None
    try:
        data = read_limited(claimed, _MARKER_MAX_BYTES)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None
    finally:
        _delete_quiet(claimed)
    if len(data) > _MARKER_MAX_BYTES:
        logger.warning("Marker %s exceeds size cap", path)
        return None
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Marker %s is not UTF-8", path)
        return None
    payload = _parse_marker(raw)
    if payload is None:
        logger.warning("Marker %s has malformed JSON", path)
        return None
    target = _validate_payload(path, payload, expected_secret, expected_tray_pid)
    if target is not None and not_before is not None and _requested_before(payload, not_before):
        logger.info("Marker %s was made before this state began; ignored", path)
        return None
    return target


def consume_quit_marker(
    data_dir: Path, expected_secret: str, expected_tray_pid: int,
) -> Optional[str]:
    """Read, validate and delete ``.quit_requested`` only; return its target.

    The restart and reset markers are not touched (issue #345: the poll
    thread must not eat the main loop's requests).
    """
    path = data_dir / MARKER_QUIT
    if not path.exists():
        return None
    return read_and_validate_marker(path, expected_secret, expected_tray_pid)


def consume_all_markers(
    data_dir: Path, expected_secret: str, expected_tray_pid: int,
    not_before: Optional[_dt.datetime] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Read, validate and delete all three markers.

    Returns ``(quit_target, reset_target, restart_target)``; each is
    ``None`` when its marker is missing or invalid. A reset or restart
    made before ``not_before`` is dropped; a quit never is. The caller
    decides which wins (``launcher.loop_decisions.ipc_precedence``).
    """
    return (
        read_and_validate_marker(data_dir / MARKER_QUIT, expected_secret, expected_tray_pid),
        read_and_validate_marker(data_dir / MARKER_RESET, expected_secret, expected_tray_pid, not_before),
        read_and_validate_marker(data_dir / MARKER_RESTART, expected_secret, expected_tray_pid, not_before),
    )
