# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Launcher-side purge of setup-phase Django sessions.

Runs between SIGTERM and respawn (FR-18a / S6) to ensure stale
``setup_phase=True`` cookies cannot replay against the post-restart
manager.

Stdlib only — Django ORM is not available from the launcher.  Reads
``db.sqlite3`` directly and decodes ``django_session.session_data``
manually.

Django's signed-session format is: ``"<b64(signature)>:<b64(json)>"``
(or similar: signature prefix followed by the base64-encoded JSON
payload).  We do a simple best-effort decode: strip anything before
the first ``:``, base64-decode, parse JSON, check for the
``setup_phase`` key.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_NAME = "db.sqlite3"
_TABLE = "django_session"


def _decode_session_data(raw: str) -> dict | None:
    """Decode Django ``session_data`` to its JSON payload.

    Returns the payload dict or ``None`` if decoding/parsing fails.
    Handles both signed (``<sig>:<payload>``) and plain formats.
    """
    if not raw:
        return None
    candidate = raw.split(":", 1)[1] if ":" in raw else raw
    try:
        # urlsafe b64 with padding tolerance
        padding = "=" * (-len(candidate) % 4)
        decoded = base64.urlsafe_b64decode(candidate + padding)
    except Exception:
        return None
    # Django may prepend a hash line before JSON; try to locate JSON.
    text = decoded.decode("utf-8", errors="replace")
    if "{" in text:
        text = text[text.index("{"):]
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None


def _row_has_setup_phase(session_data: str) -> bool:
    """True if the decoded payload carries a truthy ``setup_phase``."""
    payload = _decode_session_data(session_data)
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("setup_phase"))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def purge_setup_phase_sessions(data_dir: Path) -> int:
    """Delete setup-phase Django sessions. Returns rows deleted.

    Best-effort: returns 0 on any unexpected error (missing DB, missing
    table, corrupt sessions).  Never raises.
    """
    db_path = data_dir / "manager" / _DB_NAME
    if not db_path.exists():
        # Also try legacy data_dir/db.sqlite3 layout.
        legacy = data_dir / _DB_NAME
        if not legacy.exists():
            return 0
        db_path = legacy

    try:
        with sqlite3.connect(str(db_path)) as conn:
            if not _table_exists(conn, _TABLE):
                return 0
            cur = conn.execute(
                f"SELECT session_key, session_data FROM {_TABLE}"
            )
            to_delete: list[str] = []
            for key, data in cur.fetchall():
                if _row_has_setup_phase(data or ""):
                    to_delete.append(key)
            if not to_delete:
                return 0
            conn.executemany(
                f"DELETE FROM {_TABLE} WHERE session_key = ?",
                [(k,) for k in to_delete],
            )
            conn.commit()
            return len(to_delete)
    except sqlite3.Error as exc:
        logger.warning(
            "Failed to purge setup-phase sessions: %s", exc,
        )
        return 0
    except Exception as exc:  # defensive
        logger.warning(
            "Unexpected error during session purge: %s", exc,
        )
        return 0
