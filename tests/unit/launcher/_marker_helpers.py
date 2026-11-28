# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tray IPC marker files as the tray writes them, for the launcher tests."""

from __future__ import annotations

import datetime as _dt
import json

# Must be >= 32 chars per _validate_payload's empty-secret guard;
# production uses secrets.token_urlsafe(32) which yields ~43 chars.
SECRET = "test-launcher-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxx"
TRAY_PID = 42424

# The action each marker's payload names (launcher.tray_ipc checks it).
ACTIONS = {".quit_requested": "quit", ".restart_requested": "restart", ".reset_requested": "reset"}
_DEFAULT = object()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def write_marker(path, secret=SECRET, pid=TRAY_PID, target="manager", requested_at=None, action=_DEFAULT):
    """Write a marker payload to ``path``; ``action`` defaults to the one its name implies."""
    payload = {
        "secret": secret,
        "pid": pid,
        "action": ACTIONS.get(path.name) if action is _DEFAULT else action,
        "target": target,
        "requested_at": requested_at or now_iso(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
