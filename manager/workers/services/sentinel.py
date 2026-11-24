# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-complete sentinel file management.

The sentinel file (``.setup_complete``) is a JSON blob that marks
setup as done.  The launcher runs the setup wizard when the file is
absent; that check is existence only
(``launcher.main_dispatch._is_setup_complete``).  Its contents are
read by ``launcher.setup_sentinel`` and by
``sethlans_manager.runtime_init.cleanup_stale_setup_section``.
Workers no longer gate on it (issue #304).  All writes use atomic
write-to-temp-then-rename.

Sentinel JSON format::

    {
        "version": 1,
        "completed_at": "2025-01-15T12:00:00Z",
        "topology": "manager",
        "checkpoints": ["topology_chosen", "network_configured", ...]
    }
"""

import json
import logging
import os
import platform
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SENTINEL_FILENAME = ".setup_complete"
SENTINEL_VERSION = 1


def read_sentinel(data_dir: Path) -> dict | None:
    """Read and parse the sentinel file.

    Returns the parsed JSON dict, or ``None`` if the file is
    missing, malformed, or contains an unrecognized version.
    """
    sentinel_path = Path(data_dir) / SENTINEL_FILENAME
    if not sentinel_path.exists():
        return None
    try:
        text = sentinel_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # UnicodeDecodeError: bytes that are not valid UTF-8 (a UTF-16
        # file, a torn write) are a malformed marker like bad JSON.
        logger.warning(
            "Sentinel file at %s is malformed: %s",
            sentinel_path, exc,
        )
        return None

    if not isinstance(data, dict):
        logger.warning("Sentinel file is not a JSON object.")
        return None

    if data.get("version") != SENTINEL_VERSION:
        logger.warning(
            "Sentinel version %s not recognized (expected %s).",
            data.get("version"), SENTINEL_VERSION,
        )
        return None

    return data


def write_sentinel(data_dir: Path, data: dict) -> None:
    """Atomically write the sentinel file with ``os.fsync()``.

    On POSIX, sets permissions to 0600.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    sentinel_path = data_dir / SENTINEL_FILENAME

    content = json.dumps(data, indent=2, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(data_dir), suffix=".tmp", prefix=".sentinel_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(sentinel_path))
        # fsync parent directory for durability on POSIX
        if platform.system() != "Windows":
            dir_fd = os.open(str(data_dir), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        # Restrictive permissions on POSIX
        if platform.system() != "Windows":
            sentinel_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Sentinel written to %s", sentinel_path)


def is_setup_complete(data_dir: Path) -> bool:
    """Return ``True`` only when setup has fully finished.

    A sentinel with a missing/null ``completed_at`` is an incomplete
    record and must NOT be treated as "complete".  Only
    :func:`create_sentinel` writes a truthy ``completed_at``.
    """
    data = read_sentinel(data_dir)
    return data is not None and bool(data.get("completed_at"))


def create_sentinel(
    data_dir: Path, topology: str, checkpoints: list[str],
) -> None:
    """Create the final sentinel marking setup as complete."""
    data = {
        "version": SENTINEL_VERSION,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "topology": topology,
        "checkpoints": checkpoints,
    }
    write_sentinel(data_dir, data)
