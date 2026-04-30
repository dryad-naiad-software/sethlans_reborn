# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Filesystem cleanup helpers for the atomic install pipeline.

Two operations:

    cleanup_paths(*paths)        — best-effort delete of files / dirs;
                                   used in the failure-cleanup branches.
    cleanup_stale_partials(root) — boot-time sweep of any leftover
                                   ``<final_dir>.tmp`` / ``<final_dir>.partial``
                                   from a prior interrupted run, before
                                   the presence check.

Per spec FR §58: stale-partial sweep runs **pre-extraction within the
single parts-check thread** (the ``_thread_started`` idempotency guard
ensures only one such thread exists per process — there is no
concurrent cleanup-vs-extraction race).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, Union

logger = logging.getLogger(__name__)


def cleanup_paths(*paths: Union[str, Path]) -> None:
    """Best-effort delete each path (file or directory tree).

    Errors are logged at debug level; this function never raises.
    Used in the failure cleanup branches where surfacing an exception
    would mask the original failure cause.
    """
    for raw in paths:
        if raw is None:
            continue
        path = Path(raw)
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            logger.debug(
                "cleanup_paths: ignoring OSError on %s", path,
                exc_info=True,
            )


def cleanup_stale_partials(install_root: Path) -> None:
    """Delete leftover ``*.tmp`` / ``*.partial`` siblings of the install dir.

    ``install_root`` is e.g. ``<data_dir>/bin/ffmpeg/`` (note: parent of
    the version-pinned install dir).  We sweep every immediate child
    matching ``*.tmp`` or ``*.partial`` regardless of version pin so a
    half-finished prior-version install does not pile up indefinitely.
    """
    if not install_root.exists():
        return
    try:
        children: Iterable[Path] = list(install_root.iterdir())
    except OSError:
        logger.debug(
            "cleanup_stale_partials: cannot list %s", install_root,
            exc_info=True,
        )
        return
    for child in children:
        name = child.name
        # Match the bare-suffix forms (``.tmp`` / ``.partial``) AND the
        # archive-suffixed temp file forms (``.tmp.tar.xz`` /
        # ``.tmp.zip``) the install pipeline actually writes.
        if (
            name.endswith(".tmp")
            or name.endswith(".partial")
            or name.endswith(".tmp.tar.xz")
            or name.endswith(".tmp.zip")
            or ".tmp." in name
        ):
            cleanup_paths(child)
            logger.info(
                "cleanup_stale_partials: removed stale %s", child,
            )
