# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""HMAC-signed marker IPC for the wizard channel.

Implements the launcher half of the wizard scaffold's marker contract:

* ``write_marker`` (FR-IPC8 — ``.runtime_failed``) — the launcher's
  only marker write in Spec 1; ``.wizard_done`` is written by the
  wizard (FR-IPC1).
* ``read_marker`` (FR-IPC3) — read + validate ``.wizard_done`` (HMAC,
  type, schema_version, data_dir, freshness).

Schema MUST match ``wizard/sethlans_wizard/ipc.py`` byte-for-byte. The
launcher does NOT import from the wizard package — wizard and launcher
ship as separate PyInstaller bundles per FR-W2 — but the small parallel
implementation is cheaper than carving out a shared module.

Filesystem-side helpers (sweep, dir creation, secret-file write) live in
``launcher/wizard_dir.py`` to keep both files under the 300-line limit.

Stdlib only.
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

from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

MARKER_WIZARD_DONE = ".wizard_done"
MARKER_WIZARD_REJECT = ".wizard_reject"
MARKER_RUNTIME_FAILED = ".runtime_failed"
# v2.1-or-earlier residue still swept per FR-L0a even though no longer written.
MARKER_LEGACY_SHUTDOWN = ".wizard_shutdown"

_VALID_MARKER_TYPES = frozenset({
    "wizard_done", "wizard_reject", "runtime_failed",
})

DEFAULT_MAX_AGE_SECONDS = 60
_MARKER_MAX_BYTES = 4096
_HMAC_FIELD = "hmac_sha256"
_SCHEMA_VERSION = 1


# ---- Path / permission helpers --------------------------------------------

def _restrict_perms(path: Path, mode: int = 0o600) -> None:
    if platform.system() == "Windows":
        tighten_acls_windows(path)
        return
    try:
        os.chmod(str(path), mode)
    except OSError as exc:
        logger.warning("Could not chmod %s: %s", path, exc)


def _canonical_data_dir(data_dir: Path) -> str:
    """Mirror wizard ipc._canonical_data_dir (FR-W16)."""
    return str(Path(data_dir).resolve(strict=False))


# ---- HMAC primitives (must match wizard/sethlans_wizard/ipc.py) -----------

def _canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _compute_hmac(payload: dict, secret: bytes) -> str:
    body = {k: v for k, v in payload.items() if k != _HMAC_FIELD}
    return hmac.new(
        secret, _canonical_payload_bytes(body), hashlib.sha256,
    ).hexdigest()


# ---- Atomic write ---------------------------------------------------------

def _atomic_write_bytes(
    path: Path, data: bytes, mode: int = 0o600,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode,
    )
    try:
        os.write(fd, data)
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    _restrict_perms(path, mode)


# ---- Marker write (FR-IPC8 — .runtime_failed) -----------------------------

def write_marker(
    path: Path,
    marker_type: str,
    data_dir: Path,
    secret: bytes,
    payload: Optional[dict] = None,
) -> None:
    """Atomically write an HMAC-signed marker file at *path*."""
    if marker_type not in _VALID_MARKER_TYPES:
        raise ValueError(f"Unknown marker_type: {marker_type!r}")
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        raise ValueError("secret must be non-empty bytes")
    body: dict = dict(payload or {})
    reserved = {
        "type", "schema_version", "data_dir",
        "created_at_unix", _HMAC_FIELD,
    }
    overlap = set(body) & reserved
    if overlap:
        raise ValueError(
            f"payload may not set reserved fields: {sorted(overlap)}",
        )
    body["type"] = marker_type
    body["schema_version"] = _SCHEMA_VERSION
    body["data_dir"] = _canonical_data_dir(data_dir)
    body["created_at_unix"] = time.time()
    body[_HMAC_FIELD] = _compute_hmac(body, bytes(secret))
    _atomic_write_bytes(path, json.dumps(body).encode("utf-8"))


# ---- Marker read (FR-IPC3 — launcher consumes .wizard_done) ---------------

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


def _validate_hmac(payload: dict, secret: bytes) -> bool:
    received = payload.get(_HMAC_FIELD)
    if not isinstance(received, str):
        logger.warning("Marker missing hmac_sha256")
        return False
    expected = _compute_hmac(payload, bytes(secret))
    if not hmac.compare_digest(received, expected):
        logger.warning("Marker HMAC mismatch")
        return False
    return True


def _validate_freshness(payload: dict, max_age_seconds: int) -> bool:
    created_at = payload.get("created_at_unix")
    if not isinstance(created_at, (int, float)):
        logger.warning("Marker created_at_unix missing or non-numeric")
        return False
    age = time.time() - float(created_at)
    if age < -max_age_seconds:
        logger.warning(
            "Marker created_at_unix in the future (age=%.1fs)", age,
        )
        return False
    if age > max_age_seconds:
        logger.warning(
            "Marker is stale (age=%.1fs > %ds)", age, max_age_seconds,
        )
        return False
    return True


def _validate_payload(
    payload: dict, secret: bytes, expected_type: str,
    expected_data_dir: str, max_age_seconds: int,
) -> bool:
    if not isinstance(secret, (bytes, bytearray)) or not secret:
        logger.warning("IPC secret missing or empty; refusing marker")
        return False
    if not _validate_hmac(payload, bytes(secret)):
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
    return _validate_freshness(payload, max_age_seconds)


def read_marker(
    path: Path,
    secret: bytes,
    expected_type: str,
    data_dir: Path,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> Optional[dict]:
    """Read and validate a marker file. Returns payload dict or None."""
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
        payload, bytes(secret), expected_type,
        expected_data_dir, max_age_seconds,
    )
    if not ok:
        return None
    return payload


def delete_marker(path: Path) -> None:
    """Best-effort delete of a marker file (post-validation cleanup)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not delete marker %s: %s", path, exc)
