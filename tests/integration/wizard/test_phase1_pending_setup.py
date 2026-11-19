# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end pending_setup write through the wizard WSGI server.

Covers the integration-test agent's deferred items 1 and 2:

* **Real fsync round-trip** — the unit suite verified the ordering
  contract via mocked ``os.{open,write,fsync,close,replace}``. Here we
  drive the real wizard subprocess against a tmp filesystem, fsync the
  written file (and, on POSIX, its directory) ourselves after the WSGI
  write returns, re-read it from a fresh handle, and assert
  byte-identity. Only the file under test is flushed, never the whole
  machine (issue #273). That the product code itself fsyncs the file
  and its directory is proved in-process with a pass-through
  ``os.fsync`` spy in ``test_phase1_pending_setup_fsync.py``.
* **End-to-end WSGI pending_setup write** — POST the wizard through
  the auth → topology → admin-user → worker-password → pending-setup
  pipeline against a real subprocess, assert ``pending_setup.json``
  lands at ``<data_dir>/pending_setup.json``, parse it, and assert
  the schema contract: ``schema_version=1``, ``created_at_unix`` is a
  recent int, ``admin_user`` carries the validated tuple, and
  ``auto_enroll_local_worker`` mirrors the topology.

FFmpeg metadata is no longer carried in pending_setup.json — the
manager-side parts-check derives the binary path on boot.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import time
from pathlib import Path

import pytest

from . import _http
from ._phase1_session import open_and_select, open_session, session_headers


def _drive_wizard_to_pending_setup(wp, *, topology: str = "manager") -> Path:
    """Drive auth → topology → admin → worker-password → pending.

    Returns the path to the written ``pending_setup.json``.
    """
    session = open_and_select(wp, topology=topology)
    headers = session_headers(session)

    # Admin user (FR-M2-5) — populates wizard_state.admin in the wizard
    # subprocess.
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/admin-user/",
        {
            "username": "alice",
            "email": "alice@example.org",
            "password": "X9c!7Rq#Tv2pL@s",
            "password_confirm": "X9c!7Rq#Tv2pL@s",
        },
        headers=headers,
    )
    assert status == 200, parsed

    # Worker password (only required for manager_worker topology).
    if topology == "manager_worker":
        status, _, parsed = _http.post_json(
            f"{wp.base_url}/api/wizard/worker-password/",
            {"password": "anotherStrongPw1!"},
            headers=headers,
        )
        assert status == 200, parsed

    # Pending-setup write.
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/pending-setup/",
        {},
        headers=headers,
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed
    return wp.data_dir / "pending_setup.json"


def test_pending_setup_landed_with_correct_schema(wizard_process):
    """End-to-end WSGI write: pending_setup.json arrives with schema v1."""
    target = _drive_wizard_to_pending_setup(wizard_process, topology="manager")
    assert target.is_file(), f"missing {target}"

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1, payload
    assert payload["topology"] == "manager", payload
    assert isinstance(payload["created_at_unix"], int), payload
    # within the last 60 seconds (subprocess startup + handshake).
    now = int(time.time())
    assert now - payload["created_at_unix"] < 60, (now, payload)

    admin = payload["admin_user"]
    assert admin["username"] == "alice", payload
    assert admin["email"] == "alice@example.org", payload
    assert admin["password_plaintext"] == "X9c!7Rq#Tv2pL@s", payload

    # FFmpeg metadata is not part of the pending_setup contract any
    # more — the manager-side parts-check derives the binary path
    # itself on boot.
    assert "ffmpeg" not in payload, payload

    # manager topology never enrolls a local worker.
    assert payload["auto_enroll_local_worker"] is False, payload
    # manager topology has no worker UI password.
    assert payload["worker_ui_password_hash"] is None, payload
    assert payload["worker_ui_password_salt"] is None, payload


def test_pending_setup_manager_worker_includes_worker_pw(wizard_process):
    """manager_worker topology serializes the worker UI hash + salt."""
    target = _drive_wizard_to_pending_setup(
        wizard_process, topology="manager_worker",
    )
    assert target.is_file(), f"missing {target}"

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["topology"] == "manager_worker", payload
    assert payload["auto_enroll_local_worker"] is True, payload
    # PBKDF2 derived key length at SHA-256 → 32 bytes → 64 hex chars.
    assert isinstance(payload["worker_ui_password_hash"], str), payload
    assert len(payload["worker_ui_password_hash"]) == 64, payload
    # 16-byte salt → 32 hex chars.
    assert isinstance(payload["worker_ui_password_salt"], str), payload
    assert len(payload["worker_ui_password_salt"]) == 32, payload


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX only")
def test_pending_setup_chmod_600_on_posix(wizard_process):
    """POSIX: ``pending_setup.json`` lands with chmod 600 (FR-PEND-LIFECYCLE)."""
    target = _drive_wizard_to_pending_setup(wizard_process, topology="manager")
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)

    # No world / group bits set under any flag.
    assert not (mode & stat.S_IRWXG), oct(mode)
    assert not (mode & stat.S_IRWXO), oct(mode)


def _fsync_path(path: Path) -> None:
    """``os.fsync`` one file or directory (Windows needs a writable handle)."""
    flags = os.O_RDWR if platform.system() == "Windows" else os.O_RDONLY
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def test_pending_setup_survives_real_fsync(wizard_process):
    """Bytes are identical after a real fsync of the file and its directory.

    The wizard's atomic-write contract is: temp + fsync(file) +
    os.replace + fsync(parent dir). After the WSGI write returns we
    fsync ``pending_setup.json`` (and its directory on POSIX; Windows
    cannot open a directory for fsync) and re-read it from a fresh
    handle. The bytes must match, parse as a complete schema-v1
    payload, and no temp file may be left behind. Issue #273: this
    flushes only the file under test, not every dirty buffer on the
    machine as ``sync(1)`` did.
    """
    target = _drive_wizard_to_pending_setup(wizard_process, topology="manager")
    with target.open("rb") as fh:
        before = fh.read()

    _fsync_path(target)
    if platform.system() != "Windows":
        _fsync_path(target.parent)

    with target.open("rb") as fh:
        after = fh.read()
    assert before == after, (
        "pending_setup.json bytes diverged after fsync — "
        "atomic-write contract may be broken"
    )
    payload = json.loads(after.decode("utf-8"))
    assert payload["schema_version"] == 1, payload
    assert payload["admin_user"]["username"] == "alice", payload
    assert not target.with_name(target.name + ".tmp").exists()


def test_pending_setup_idempotent_under_repeated_post(wizard_process):
    """Two ``POST /pending-setup/`` calls produce the same content.

    Re-uses the same session token because opening a fresh auth
    session invalidates any prior session — see ``handlers/auth.py``.
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    headers = session_headers(session)

    # Drive the full pipeline once.
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/admin-user/",
        {
            "username": "alice",
            "email": "alice@example.org",
            "password": "X9c!7Rq#Tv2pL@s",
            "password_confirm": "X9c!7Rq#Tv2pL@s",
        },
        headers=headers,
    )
    assert status == 200, parsed
    target = wp.data_dir / "pending_setup.json"

    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/pending-setup/",
        {},
        headers=headers,
    )
    assert status == 200, parsed
    first_payload = json.loads(target.read_bytes().decode("utf-8"))

    # Re-call pending-setup on the same session — the wizard subprocess
    # still has the in-memory admin tuple.
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/pending-setup/",
        {},
        headers=headers,
    )
    assert status == 200, parsed
    second_payload = json.loads(target.read_bytes().decode("utf-8"))

    # The schema-bearing fields match.
    assert second_payload["topology"] == first_payload["topology"]
    assert second_payload["admin_user"] == first_payload["admin_user"]
    assert second_payload["schema_version"] == first_payload["schema_version"]
    # ``created_at_unix`` may advance on the second write — that is
    # documented FR-PEND2 behaviour (the field captures *each* write).
    assert isinstance(second_payload["created_at_unix"], int)


def test_pending_setup_refuses_without_topology(wizard_process):
    """Missing topology.json → 400, no file written."""
    wp = wizard_process
    session = open_session(wp)
    # Skip topology selection.
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/pending-setup/",
        {},
        headers=session_headers(session),
    )
    assert status == 400, parsed
    assert "topology" in (parsed.get("error") or "").lower(), parsed
    assert not (wp.data_dir / "pending_setup.json").exists()
