# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bootstrap persistence for Django's ``SECRET_KEY``.

On first boot, the manager has no persisted key and no env-var / ini
override. Rather than crashing (Issue #63), we generate a fresh key,
write it atomically to the manager data directory with restrictive
permissions, and return it. Subsequent boots read the same file.

Callers should still prefer explicit overrides (env var or
``manager.ini``) when provided; this loader is the fallback chain.
"""

import logging
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

logger = logging.getLogger(__name__)

SECRET_KEY_FILENAME = "secret_key"


def _restrict_permissions(path: Path) -> None:
    """Tighten permissions on the persisted key file.

    POSIX: chmod 0o600. Windows: ``icacls`` inheritance reset + grant
    to the current user only. Failures are logged but not fatal — a
    key with looser permissions is still better than a crash.
    """
    if platform.system() == "Windows":
        try:
            subprocess.run(
                [
                    "icacls", str(path), "/inheritance:r",
                    "/grant:r", f"{os.getlogin()}:F",
                ],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning(
                "Could not set ACL on SECRET_KEY file %s: %s", path, exc,
            )
        return
    try:
        os.chmod(str(path), 0o600)
    except OSError as exc:
        logger.warning(
            "Could not chmod SECRET_KEY file %s: %s", path, exc,
        )


def _atomic_write(path: Path, contents: str) -> None:
    """Write ``contents`` to ``path`` atomically.

    Writes to a tmp sibling in the same directory, then renames. The
    tmp file is created via ``tempfile.mkstemp`` so a crashed prior
    run never leaves a predictable ``.tmp`` name conflicting with us.
    """
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contents)
        _restrict_permissions(tmp_path)
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def load_or_create_secret_key(data_dir: Path) -> str:
    """Return the persisted SECRET_KEY, generating one on first boot.

    Parameters
    ----------
    data_dir : Path
        The manager's per-user data directory
        (``get_data_dir('manager')``).

    Returns
    -------
    str
        A Django-compatible secret key.

    Raises
    ------
    ImproperlyConfigured
        If the key file cannot be created (disk full, permission
        denied, etc.). The error mentions the target path so the
        operator can fix the underlying issue. We never silently fall
        back to the insecure default key in this path.
    """
    data_dir = Path(data_dir)
    key_path = data_dir / SECRET_KEY_FILENAME

    if key_path.exists():
        try:
            existing = key_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(
                f"Could not read SECRET_KEY file at {key_path}: {exc}"
            )
        if existing:
            return existing
        # Empty file — fall through and regenerate.

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Could not create SECRET_KEY directory {data_dir}: {exc}"
        )

    new_key = get_random_secret_key()
    try:
        _atomic_write(key_path, new_key)
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Could not write SECRET_KEY file at {key_path}: {exc}"
        )

    logger.info("Generated and persisted new SECRET_KEY at %s", key_path)
    return new_key
