# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard data-directory layout, secret-file writes, and stale sweep.

Filesystem-side helpers for the launcher's wizard channel:

* ``ensure_wizard_dir`` (FR-L6) — create ``<data_dir>/wizard/`` with
  ``0o700`` on POSIX / restricted ACL on Windows.
* ``sweep_stale_markers`` (FR-L0a) — delete leftover wizard markers
  from a prior crashed run.
* ``write_secret_file`` (FR-L3a / FR-L4a) — atomically write the setup
  token / IPC HMAC secret with chmod-600-equivalent perms.

Kept separate from ``launcher/wizard_ipc.py`` (which owns the
HMAC-signed marker primitives) so neither file crosses the 300-line
limit.

Stdlib only.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

from shared.file_acls import tighten_acls_windows

logger = logging.getLogger(__name__)

# Marker filenames re-exported from wizard_ipc so the sweep set is the
# single source of truth.
from launcher.wizard_ipc import (  # noqa: E402
    MARKER_LEGACY_SHUTDOWN,
    MARKER_RUNTIME_FAILED,
    MARKER_WIZARD_DONE,
    MARKER_WIZARD_REJECT,
)

_ALL_MARKERS = (
    MARKER_WIZARD_DONE,
    MARKER_WIZARD_REJECT,
    MARKER_RUNTIME_FAILED,
    MARKER_LEGACY_SHUTDOWN,
)


def wizard_dir(data_dir: Path) -> Path:
    """Return the wizard IPC marker directory for *data_dir*."""
    return Path(data_dir) / "wizard"


def _restrict_perms(path: Path, mode: int) -> None:
    if platform.system() == "Windows":
        tighten_acls_windows(path)
        return
    try:
        os.chmod(str(path), mode)
    except OSError as exc:
        logger.warning("Could not chmod %s: %s", path, exc)


def ensure_wizard_dir(data_dir: Path) -> Path:
    """Create ``<data_dir>/wizard/`` (mode 0o700 on POSIX) per FR-L6."""
    target = wizard_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    _restrict_perms(target, 0o700)
    return target


def sweep_stale_markers(data_dir: Path) -> None:
    """Delete leftover wizard markers from a prior crashed run.

    Mirrors ``launcher.tray_ipc.sweep_stale_markers``: silent on
    ``FileNotFoundError``, WARN on other ``OSError``.
    """
    target_dir = wizard_dir(data_dir)
    if not target_dir.exists():
        return
    for name in _ALL_MARKERS:
        path = target_dir / name
        try:
            path.unlink()
            logger.info("Swept stale wizard marker %s", path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not sweep %s: %s", path, exc)


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
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


def write_secret_file(path: Path, value: bytes) -> None:
    """Write *value* atomically with chmod-600-equivalent perms.

    Used for ``<data_dir>/wizard/.setup_token`` (FR-L3a) and
    ``<data_dir>/wizard/.ipc_secret`` (FR-L4a). The wizard reads each
    file at startup and immediately ``os.unlink()``s it (FR-W6 /
    SEC-MED-11).
    """
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("value must be bytes")
    _atomic_write_bytes(path, bytes(value))


def cleanup_wizard_dir(data_dir: Path) -> None:
    """Best-effort recursive delete of ``<data_dir>/wizard/`` (FR-L13).

    Called after successful ``.wizard_done`` processing to remove TLS
    files, secret/token files, port file, log, and marker files.
    Errors are logged at WARN; caller proceeds regardless.
    """
    import shutil

    target = wizard_dir(data_dir)
    if not target.exists():
        return
    try:
        shutil.rmtree(str(target))
    except OSError as exc:
        logger.warning("Could not clean up wizard dir %s: %s", target, exc)
