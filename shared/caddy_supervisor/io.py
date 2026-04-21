# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Atomic text-file write helper for the Caddyfile.

Inlined in the ``caddy_supervisor`` package rather than extracted to a
shared helper because the existing helpers
(``launcher/setup_helpers.py:atomic_write_ini`` and
``worker/sethlans_worker_agent/config_store/io.py:atomic_write``)
are typed to ``ConfigParser`` and ``dict`` respectively; a shared
extraction would churn both modules for no runtime benefit in this
phase. A future cross-component refactor (Phase 5d or later) could
promote this to ``shared/atomic_io.py``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    The parent directory is created if missing. The temp file is
    written in the same directory as the target so ``os.replace`` is
    atomic on both POSIX and Windows. On write failure the temp file
    is best-effort unlinked so we do not leak ``.tmp`` crumbs.

    :raises ValueError: ``content`` is empty.
    """
    if not content:
        raise ValueError("Caddyfile content must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".caddyfile_", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
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
