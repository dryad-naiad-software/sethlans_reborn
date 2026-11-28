# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The launcher's files in ``<settings>/setup/`` and its one settings-file edit.

* ``setup_token`` (FR-SEC-1): a fresh ``secrets.token_urlsafe(32)`` on
  each ``SETUP`` entry; after a failed first start the same token is
  written again, byte for byte, so the open setup page keeps working
  (FR-LN-6). Setup mode reads it once at start; the tray reads it for
  Copy Setup Token. Deleted when setup finishes.
* ``last_failure`` (FR-LN-6): one line, the reason the manager gave;
  setup mode shows it in ``/api/health/`` and on the form.
  :func:`clean_reason` is the one cleaner of every reason the launcher
  writes or shows (control characters out, one line, capped).
* ``launcher_state.json``: the loop state, the port and the reason, for
  the tray.
* :func:`remove_completed_at` (FR-RS-1, FR-SF-3): Reset removes
  ``[setup] completed_at`` and nothing else.

Every file is written whole and atomically, owner-only from birth
(``shared.settings_file_io.atomic_replace``), in a ``setup/`` folder
created owner-only. Nothing here logs the token.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

from shared import settings_file
from shared.settings_file_io import atomic_replace, ensure_owner_only_dir

TOKEN_FILE_NAME = settings_file.SETUP_TOKEN_NAME
LAST_FAILURE_NAME = settings_file.LAST_FAILURE_NAME
STATE_FILE_NAME = "launcher_state.json"
# Well inside the 4096 bytes setup mode reads of last_failure (leftovers.MAX_FAILURE_BYTES).
MAX_REASON_CHARS = 500


def setup_dir() -> Path:
    """``<settings>/setup``."""
    return settings_file.get_settings_dir() / settings_file.SETUP_SUBDIR


def _write(name: str, data: bytes) -> Path:
    folder = setup_dir()
    ensure_owner_only_dir(folder.parent)
    ensure_owner_only_dir(folder)
    path = folder / name
    atomic_replace(path, data)
    return path


def _delete(name: str) -> bool:
    try:
        (setup_dir() / name).unlink()
    except FileNotFoundError:
        return False
    return True


def write_fresh_token() -> str:
    """Write a new setup token and return it (each ``SETUP`` entry, FR-SEC-1)."""
    token = secrets.token_urlsafe(32)
    rewrite_token(token)
    return token


def rewrite_token(token: str) -> None:
    """Write ``token`` again exactly as given (``SETUP`` after a failed first start)."""
    if not token or not token.isascii() or token != token.strip():
        raise ValueError("A setup token is a non-empty ASCII string without outer spaces")
    _write(TOKEN_FILE_NAME, token.encode("ascii"))


def delete_token() -> bool:
    """Delete the setup token (setup finished); True when one was there."""
    return _delete(TOKEN_FILE_NAME)


def clean_reason(reason: str) -> str:
    """``reason`` as one line: control characters become spaces, runs of
    spaces collapse, capped at :data:`MAX_REASON_CHARS`."""
    printable = "".join(ch if ch.isprintable() else " " for ch in str(reason))
    return " ".join(printable.split())[:MAX_REASON_CHARS]


def write_last_failure(reason: str) -> None:
    """Write the first start's failure reason, as one capped line."""
    _write(LAST_FAILURE_NAME, clean_reason(reason).encode("utf-8"))


def clear_last_failure() -> bool:
    """Delete ``last_failure``; True when one was there."""
    return _delete(LAST_FAILURE_NAME)


def write_launcher_state(state: str, *, port: Optional[int] = None, reason: Optional[str] = None) -> None:
    """Write ``launcher_state.json`` ``{"state", "port", "reason"}`` for the tray."""
    payload = {
        "state": state,
        "port": port,
        "reason": None if reason is None else clean_reason(reason),
    }
    _write(STATE_FILE_NAME, json.dumps(payload).encode("utf-8"))


def remove_completed_at(path=None) -> bool:
    """Remove ``[setup] completed_at`` only (Reset, FR-RS-1); True when it was there.

    Goes through the locked, atomic, owner-only writer. Every other
    section and key stays as it was; a missing file is left missing.
    """
    return settings_file.remove_key(
        settings_file.SECTION_SETUP, settings_file.KEY_SETUP_COMPLETED_AT, path,
    )
