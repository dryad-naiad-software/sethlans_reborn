# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cross-process file-permission helpers.

The Sethlans launcher, manager, worker, and wizard all need to
restrict on-disk secret files (TLS keys, IPC secrets, setup tokens)
to the owning user. POSIX uses ``os.chmod(path, 0o600)``; Windows
delegates to pywin32's ACL APIs when available.

``SECRET_WRITE_FLAGS`` is the ``os.open`` flag set those writers use
to create or truncate a file for writing. It includes ``O_BINARY``
where the platform has it: on Windows a descriptor opened without it
is in text mode, and ``os.write`` would turn every ``\\n`` into
``\\r\\n``, so the file would not hold the bytes the caller wrote.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SECRET_WRITE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
)


def tighten_acls_windows(path: Path) -> None:
    """Optionally restrict *path* to the current user on Windows."""
    if sys.platform != "win32":
        return
    try:
        import win32security  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "pywin32 unavailable; cannot tighten ACLs on %s", path,
        )
        return
    try:  # pragma: no cover
        user, _domain, _type = win32security.LookupAccountName(
            None, os.environ.get("USERNAME", ""),
        )
        sd = win32security.SECURITY_DESCRIPTOR()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            0x1F01FF,  # FILE_ALL_ACCESS
            user,
        )
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION,
            sd,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("ACL tighten failed on %s: %s", path, exc)
