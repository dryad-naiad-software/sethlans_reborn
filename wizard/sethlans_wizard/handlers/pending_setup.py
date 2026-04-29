# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/pending-setup/`` — write ``pending_setup.json`` (FR-PEND2).

Reads the in-memory wizard step state (admin tuple, worker UI password
hash + salt, FFmpeg metadata, topology) and serializes it to
``<data_dir>/pending_setup.json`` atomically with the FR-PEND1a fsync
sequence:

1. write JSON to a temp file in the same directory
2. ``os.fsync(temp_fd)``
3. close temp fd
4. ``os.replace(temp, final)``
5. ``os.fsync(parent_dir_fd)`` on POSIX (no-op on Windows)

The handler MUST NOT return 200 until step 5 completes.

Permissions: chmod 600 on POSIX, ``tighten_acls_windows`` on Windows.

Logging discipline (FR-PEND3): log only path + byte count. NEVER the
file body.

Idempotent under the singleton handoff-state lock — the lock is held
ONLY around the read+serialize+write critical section.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Callable, Iterable

from shared.file_acls import tighten_acls_windows
from wizard.sethlans_wizard import auth_state, wizard_state
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)

PENDING_SETUP_FILENAME = "pending_setup.json"
PENDING_SCHEMA_VERSION = 1


def _read_topology(data_dir: Path) -> str | None:
    target = data_dir / "topology.json"
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    topo = payload.get("topology")
    return topo if isinstance(topo, str) else None


def _build_payload(topology: str) -> dict:
    """Serialize the in-memory wizard state into the FR-PEND1 schema."""
    admin = wizard_state.get_admin()
    if admin is None:
        raise ValueError("admin tuple not set")
    ffmpeg = wizard_state.get_ffmpeg()
    if ffmpeg is None:
        raise ValueError("ffmpeg metadata not set")
    worker_pw = wizard_state.get_worker_password()
    payload: dict = {
        "schema_version": PENDING_SCHEMA_VERSION,
        "topology": topology,
        "created_at_unix": int(time.time()),
        "admin_user": {
            "username": admin["username"],
            "email": admin["email"],
            "password_plaintext": admin["password_plaintext"],
        },
        "worker_ui_password_hash": (
            worker_pw["hash"] if worker_pw else None
        ),
        "worker_ui_password_salt": (
            worker_pw["salt"] if worker_pw else None
        ),
        "ffmpeg": {
            "version": ffmpeg["version"],
            "binary_path": ffmpeg["binary_path"],
        },
        "auto_enroll_local_worker": topology == "manager_worker",
    }
    return payload


def _atomic_write(target: Path, body: bytes) -> None:
    """FR-PEND1a fsync sequence + chmod 600 / Windows ACL tighten."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    if platform.system() != "Windows":
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
        except OSError as exc:
            logger.warning(
                "Could not open dir for fsync %s: %s", target.parent, exc,
            )
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        try:
            os.chmod(str(target), 0o600)
        except OSError as exc:
            logger.warning("Could not chmod %s: %s", target, exc)
    else:
        tighten_acls_windows(target)


def make_pending_setup_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-PEND2."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )
    if _wsgi.query_string_has_forbidden_key(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "session token must not appear in URL"},
            status=400,
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    topology = _read_topology(data_dir)
    if topology is None:
        return _wsgi.send_json(
            start_response,
            {"error": "topology not yet selected"},
            status=400,
        )
    if topology not in ("manager", "manager_worker"):
        # worker_only does not write pending_setup.json.
        return _wsgi.send_json(
            start_response,
            {"error": "pending_setup not applicable for this topology"},
            status=400,
        )

    # FR-PEND2 idempotency under singleton handoff-state lock.
    target = data_dir / PENDING_SETUP_FILENAME
    handoff_lock = auth_state.get_handoff_lock()
    with handoff_lock:
        try:
            payload = _build_payload(topology)
        except ValueError as exc:
            logger.warning("pending_setup build failed: %s", exc)
            return _wsgi.send_json(
                start_response,
                {"error": "wizard_state_incomplete",
                 "message": str(exc)},
                status=400,
            )
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        try:
            _atomic_write(target, body)
        except OSError as exc:
            logger.error(
                "Could not write %s: %s", target, exc,
            )
            return _wsgi.send_json(
                start_response,
                {"error": "could not write pending_setup.json"},
                status=500,
            )
    # FR-PEND3 — log path + byte count only, never the body.
    logger.info("Wrote %s (%d bytes)", target, len(body))
    return _wsgi.send_json(
        start_response,
        {"status": "ok"},
        status=200,
    )


__all__ = [
    "PENDING_SETUP_FILENAME",
    "PENDING_SCHEMA_VERSION",
    "make_pending_setup_handler",
]
