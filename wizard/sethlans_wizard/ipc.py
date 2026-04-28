# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""HMAC-signed JSON file-marker IPC for the wizard.

Implements FR-IPC1 (.wizard_done), FR-IPC4 (.wizard_reject), and
FR-IPC8 (.runtime_failed) of the Spec 1 wizard scaffold. Note that
``.wizard_shutdown`` was REMOVED in v2.2 — do NOT add it.

HMAC framing mirrors ``launcher/tray_ipc.py`` (NF-8): canonical JSON
(sorted keys, no whitespace) of the payload minus the ``hmac_sha256``
field, signed with ``hashlib.sha256``. ``data_dir`` is a signed field
(SEC-MED-9); both sides normalise via ``Path(...).resolve(strict=False)``.

Marker files are written atomically (temp + ``os.replace`` in the same
dir) with ``chmod 600`` on POSIX / ACL tightening on Windows.
``read_secret_file`` is the SEC-MED-11 helper that reads the launcher's
chmod-600 secret file and immediately ``unlink``s it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Optional

# shared/file_acls owns the canonical Windows ACL helper for chmod-600-
# equivalent filesystem objects; reuse per FR-IPC6 / NF-8 ("reuse the
# canonical JSON + hmac.new(..., hashlib.sha256) pattern from
# launcher/tray_ipc.py").
from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

MARKER_WIZARD_DONE = ".wizard_done"
MARKER_WIZARD_REJECT = ".wizard_reject"
MARKER_RUNTIME_FAILED = ".runtime_failed"

_VALID_MARKER_TYPES = frozenset({
    "wizard_done",
    "wizard_reject",
    "runtime_failed",
})

# FR-IPC1 / FR-IPC4 / FR-IPC8 freshness window.
DEFAULT_MAX_AGE_SECONDS = 60

# Defensive payload size cap; mirrors tray_ipc._MARKER_MAX_BYTES.
_MARKER_MAX_BYTES = 4096

_HMAC_FIELD = "hmac_sha256"
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------
# Path / permission helpers
# ---------------------------------------------------------------------

def _restrict_perms(path: Path, mode: int) -> None:
    """Restrict *path* to the owning user.

    POSIX: ``os.chmod(path, mode)``. Windows: delegate to the tray ACL
    helper (mode is ignored on Windows; ACL grants the current user
    full control and revokes inheritance).
    """
    if platform.system() == "Windows":
        tighten_acls_windows(path)
    else:
        try:
            os.chmod(str(path), mode)
        except OSError as exc:
            logger.warning("Could not chmod %s: %s", path, exc)


def _canonical_data_dir(data_dir: Path) -> str:
    """Return the canonical absolute path string for *data_dir*.

    Mirrors the FR-W16 / FR-IPC1 normalisation:
    ``Path(...).resolve(strict=False)``. Stringified for JSON.
    """
    return str(Path(data_dir).resolve(strict=False))


# ---------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------

def _canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON encoding for HMAC: sorted keys, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_hmac(payload: dict, secret: bytes) -> str:
    """Return hex SHA-256 HMAC over the payload (excluding the HMAC field)."""
    body = {k: v for k, v in payload.items() if k != _HMAC_FIELD}
    return hmac.new(secret, _canonical_payload_bytes(body), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------

def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomic temp-file-plus-``os.replace`` write, then tighten perms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        mode,
    )
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    _restrict_perms(path, mode)


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def write_marker(
    path: Path,
    marker_type: str,
    data_dir: Path,
    secret: bytes,
    payload: Optional[dict] = None,
) -> None:
    """Atomically write an HMAC-signed marker file at *path*.

    Args:
        path: Marker file to create (e.g. ``<data_dir>/wizard/.wizard_done``).
        marker_type: One of ``wizard_done``, ``wizard_reject``,
            ``runtime_failed`` (FR-IPC1 / FR-IPC4 / FR-IPC8).
        data_dir: The data directory to bind to the marker (SEC-MED-9).
            Will be canonicalised via ``Path.resolve(strict=False)``.
        secret: HMAC secret bytes (the IPC HMAC secret).
        payload: Optional extra fields to embed (e.g. ``topology``,
            ``wizard_port``, ``wizard_pid`` for ``wizard_done``;
            ``reason`` for ``wizard_reject`` / ``runtime_failed``).
            ``type``, ``schema_version``, ``data_dir``,
            ``created_at_unix``, and ``hmac_sha256`` are populated by
            this function and MUST NOT appear in *payload*.

    Raises:
        ValueError: If *marker_type* is unknown or *payload* contains a
            reserved field.
    """
    if marker_type not in _VALID_MARKER_TYPES:
        raise ValueError(f"Unknown marker_type: {marker_type!r}")
    body: dict = dict(payload or {})
    reserved = {"type", "schema_version", "data_dir", "created_at_unix", _HMAC_FIELD}
    overlap = set(body) & reserved
    if overlap:
        raise ValueError(f"payload may not set reserved fields: {sorted(overlap)}")
    body["type"] = marker_type
    body["schema_version"] = _SCHEMA_VERSION
    body["data_dir"] = _canonical_data_dir(data_dir)
    body["created_at_unix"] = time.time()
    body[_HMAC_FIELD] = _compute_hmac(body, secret)
    _atomic_write_bytes(path, json.dumps(body).encode("utf-8"))


def _parse_marker(raw: bytes) -> Optional[dict]:
    if len(raw) > _MARKER_MAX_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _validate_payload(
    payload: dict,
    secret: bytes,
    expected_type: str,
    expected_data_dir: str,
    max_age_seconds: int,
) -> bool:
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        logger.warning("IPC secret missing or empty; refusing marker")
        return False
    received_hmac = payload.get(_HMAC_FIELD)
    if not isinstance(received_hmac, str):
        logger.warning("Marker missing hmac_sha256")
        return False
    expected_hmac = _compute_hmac(payload, secret)
    if not hmac.compare_digest(received_hmac, expected_hmac):
        logger.warning("Marker HMAC mismatch")
        return False
    if payload.get("type") != expected_type:
        logger.warning(
            "Marker type=%r != expected=%r",
            payload.get("type"), expected_type,
        )
        return False
    if payload.get("schema_version") != _SCHEMA_VERSION:
        logger.warning(
            "Marker schema_version=%r != %d",
            payload.get("schema_version"), _SCHEMA_VERSION,
        )
        return False
    if payload.get("data_dir") != expected_data_dir:
        logger.warning(
            "Marker data_dir=%r != expected=%r",
            payload.get("data_dir"), expected_data_dir,
        )
        return False
    created_at = payload.get("created_at_unix")
    if not isinstance(created_at, (int, float)):
        logger.warning("Marker created_at_unix missing or non-numeric")
        return False
    age = time.time() - float(created_at)
    if age < -max_age_seconds:
        logger.warning("Marker created_at_unix is in the future (age=%.1fs)", age)
        return False
    if age > max_age_seconds:
        logger.warning("Marker is stale (age=%.1fs > %ds)", age, max_age_seconds)
        return False
    return True


def read_marker(
    path: Path,
    secret: bytes,
    expected_type: str,
    data_dir: Path,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> Optional[dict]:
    """Read and validate a marker file.

    Returns the validated payload dict on success, ``None`` on any
    validation failure (missing file, malformed JSON, HMAC mismatch,
    wrong type, wrong schema_version, wrong data_dir, stale).

    The caller is responsible for any post-read deletion (FR-IPC3 for
    ``.wizard_done``; the wizard's FR-W17 / FR-W14 polling deletes
    invalid markers it observes).
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read marker %s: %s", path, exc)
        return None
    payload = _parse_marker(raw)
    if payload is None:
        logger.warning("Marker %s malformed or oversized", path)
        return None
    expected_data_dir = _canonical_data_dir(data_dir)
    ok = _validate_payload(
        payload, secret, expected_type, expected_data_dir, max_age_seconds,
    )
    if not ok:
        return None
    return payload


def read_secret_file(path: Path) -> bytes:
    """Read a launcher-written secret file and immediately ``unlink`` it.

    Implements the wizard side of SEC-MED-11: the wizard reads the
    chmod-600 IPC secret file and the chmod-600 setup-token file, then
    deletes them so the disk-exposure window is the read latency, not
    the full handoff lifetime.

    The returned bytes have leading/trailing whitespace stripped.
    Raises ``OSError`` if the file is unreadable; the caller is expected
    to translate that into a clean wizard exit.
    """
    raw = Path(path).read_bytes()
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(
            "Could not unlink secret file %s after read: %s", path, exc,
        )
    return raw.strip()
