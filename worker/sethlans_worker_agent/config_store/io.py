# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
I/O primitives for the worker config store:

  * Atomic write via tempfile + ``os.replace``.
  * Restrictive file and directory permissions (``chmod 0o600`` /
    ``icacls /inheritance:r``) — best-effort with errors logged
    (FR-26).
  * Cross-process advisory file lock on a sidecar file via
    ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows.
  * JSON read/merge helpers (whole-file reads are lock-free per
    FR-27).
"""

import json
import logging
import os
import platform
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .paths import SYSTEM_CONFIG_PATH

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.05


class ConfigLockTimeoutError(RuntimeError):
    """Raised when the cross-process config lock cannot be acquired."""


def ensure_parent_dir(path: Path) -> None:
    """Create the parent directory and apply restrictive permissions."""
    parent = path.parent
    newly_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if not newly_created:
        return
    if platform.system() == "Windows":
        _apply_windows_acl(parent)
    else:
        try:
            os.chmod(parent, 0o700)
        except OSError as e:
            logger.error(
                "Failed to chmod config parent %s: %s", parent, e,
            )


def _apply_windows_acl(parent: Path) -> None:
    user = os.environ.get("USERNAME") or ""
    if not user:
        try:
            user = os.getlogin()
        except OSError:
            user = ""
    if not user:
        logger.error(
            "Could not determine Windows user for icacls; "
            "skipping ACL on %s", parent,
        )
        return
    try:
        subprocess.run(
            [
                "icacls", str(parent),
                "/inheritance:r",
                "/grant:r", f"{user}:F",
            ],
            check=True, capture_output=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.error(
            "Failed to restrict config directory ACL on %s: %s",
            parent, e,
        )


def restrict_file_permissions(path: Path) -> None:
    if platform.system() == "Windows":
        return  # Parent directory ACL already restricts the file.
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.error("Failed to chmod config file %s: %s", path, e)


def atomic_write(path: Path, data: dict) -> None:
    ensure_parent_dir(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".config_", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    restrict_file_permissions(path)


def read_json_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read config file %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "Config file %s did not contain a JSON object", path,
        )
        return {}
    return data


def load_system_config() -> dict:
    path: Optional[Path] = SYSTEM_CONFIG_PATH
    if path is None or not path.exists():
        return {}
    try:
        st = path.stat()
    except OSError as e:
        logger.warning("Could not stat system config %s: %s", path, e)
        return {}
    if stat.S_IMODE(st.st_mode) & 0o004:
        logger.warning(
            "system-wide config %s is world-readable; refusing to load",
            path,
        )
        return {}
    return read_json_file(path)


def deep_merge(base: dict, overlay: dict) -> dict:
    """Return ``base`` with ``overlay`` recursively merged on top."""
    out = dict(base)
    for key, value in overlay.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class CrossProcessLock:
    """Acquire an advisory file lock on the sidecar lockfile."""

    def __init__(self, lockfile: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
        self._lockfile = lockfile
        self._timeout = timeout
        self._fh = None

    def __enter__(self) -> "CrossProcessLock":
        ensure_parent_dir(self._lockfile)
        self._fh = open(self._lockfile, "a+b")
        deadline = time.monotonic() + self._timeout
        if os.name == "nt":
            self._acquire_windows(deadline)
        else:
            self._acquire_posix(deadline)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fh is not None:
                self._release()
        finally:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None

    def _release(self) -> None:
        if os.name == "nt":
            try:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            try:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass

    def _acquire_posix(self, deadline: float) -> None:
        import fcntl
        while True:
            try:
                fcntl.flock(
                    self._fh.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConfigLockTimeoutError(
                        "Timed out acquiring config file lock"
                    )
                time.sleep(_LOCK_POLL_INTERVAL)

    def _acquire_windows(self, deadline: float) -> None:
        import msvcrt
        while True:
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise ConfigLockTimeoutError(
                        "Timed out acquiring config file lock"
                    )
                time.sleep(_LOCK_POLL_INTERVAL)
