# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``.setup_progress.json`` reader / writer (FR-CHK1 / FR-CHK1a).

The wizard records each completed step into
``<data_dir>/.setup_progress.json``. The schema is the v1 envelope from
FR-CHK1::

    {
        "schema_version": 1,
        "topology": "<topology>",
        "checkpoints": ["<name>", "<name>", ...]
    }

``append_checkpoint`` is the read-modify-write entry point. Per
FR-CHK1a it serializes the read+write under the **process-wide
progress-file lock** declared at module scope; without serialization,
two concurrent step handlers race, both read the same prior array, and
one's append is silently lost on the next write.

Lock-acquisition order vs. ``auth_state``'s singleton handoff-state
lock: the progress-file lock is ALWAYS acquired AFTER the handoff-state
lock if both are needed. In practice no Phase 1 handler holds both, but
documenting the order here prevents a future AB-BA inversion.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Optional

from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

PROGRESS_FILENAME = ".setup_progress.json"
PROGRESS_SCHEMA_VERSION = 1

# FR-CHK1a — per-process progress-file lock. Held during the full
# read-modify-write sequence (read existing JSON, parse, idempotent
# early-return if name already present, append, atomic write).
_progress_file_lock: threading.Lock = threading.Lock()

# Windows transient-PermissionError retry policy (FR-CHK3-RESUME):
# os.replace can race with a concurrent open() and raise transiently.
_WINDOWS_READ_RETRIES = 3
_WINDOWS_READ_BACKOFF_SECONDS = 0.05


def get_progress_lock() -> threading.Lock:
    """Return the singleton progress-file lock (FR-CHK1a)."""
    return _progress_file_lock


def _read_locked(path: Path) -> dict:
    """Read and parse the progress file. Caller MUST hold the lock."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning("Corrupt progress file at %s; treating as empty", path)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_atomic_locked(path: Path, payload: dict) -> None:
    """Atomically write *payload* to *path* under the FR-PEND1a fsync sequence.

    Caller MUST hold the progress-file lock. Sequence:
    write to ``path.tmp`` → fsync(temp_fd) → close → os.replace →
    fsync(parent_dir_fd) on POSIX (no-op on Windows).
    """
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
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
    os.replace(str(tmp), str(path))
    if platform.system() != "Windows":
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError as exc:
            logger.warning("Could not open dir for fsync %s: %s", path.parent, exc)
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        try:
            os.chmod(str(path), 0o600)
        except OSError as exc:
            logger.warning("Could not chmod %s: %s", path, exc)
    else:
        tighten_acls_windows(path)


def append_checkpoint(
    data_dir: Path,
    name: str,
    topology: Optional[str] = None,
) -> bool:
    """Append *name* to ``.setup_progress.json`` (FR-CHK1).

    Idempotent: if *name* already appears in the array the function
    returns False and skips the write. Returns True if a new entry was
    added.

    *topology* is recorded on the first append (or backfilled if the
    existing payload had a ``null`` topology); subsequent calls leave
    the existing value alone.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(name, str) or not name:
        raise ValueError("checkpoint name must be a non-empty str")
    target = data_dir / PROGRESS_FILENAME
    with _progress_file_lock:
        payload = _read_locked(target)
        existing = payload.get("checkpoints")
        if not isinstance(existing, list):
            existing = []
        if name in existing:
            logger.debug("Checkpoint %s already present; idempotent no-op", name)
            return False
        new_payload = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "topology": payload.get("topology") or topology,
            "checkpoints": list(existing) + [name],
        }
        _write_atomic_locked(target, new_payload)
        logger.info("Recorded checkpoint %s", name)
        return True


def read_checkpoints(data_dir: Path) -> dict:
    """Return the parsed progress payload (or an empty dict).

    Read under the progress-file lock to avoid a partial-write race.
    On Windows ``os.replace`` can raise transient ``PermissionError``
    if a concurrent reader catches the in-flight rename — retry up to
    ``_WINDOWS_READ_RETRIES`` times with a short backoff per
    FR-CHK3-RESUME.
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    target = data_dir / PROGRESS_FILENAME
    attempts = _WINDOWS_READ_RETRIES if platform.system() == "Windows" else 1
    last_exc: Optional[BaseException] = None
    for attempt in range(attempts):
        with _progress_file_lock:
            try:
                return _read_locked(target)
            except OSError as exc:  # pragma: no cover — handled in _read_locked
                last_exc = exc
                if exc.errno not in (errno.EACCES, errno.EPERM):
                    return {}
        time.sleep(_WINDOWS_READ_BACKOFF_SECONDS)
    if last_exc is not None:
        logger.warning("Giving up reading %s: %s", target, last_exc)
    return {}


__all__ = [
    "PROGRESS_FILENAME",
    "PROGRESS_SCHEMA_VERSION",
    "append_checkpoint",
    "read_checkpoints",
    "get_progress_lock",
]
