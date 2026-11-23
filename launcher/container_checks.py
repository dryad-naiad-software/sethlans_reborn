# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start-up checks for the manager Docker image (issue #297).

The image keeps all manager state in ``SETHLANS_MANAGER_DATA_DIR``
(``/data``). :func:`data_dir_problem` runs before anything else in the
container entrypoint:

1. The setting must be present and an absolute path, else exit
   :data:`EXIT_DATA_DIR_INVALID` with a configuration message.
2. The folder must be writable. On a Linux host Docker creates a missing
   bind-mount folder owned by root, and the manager (uid 1000) then
   cannot write it; exit :data:`EXIT_DATA_DIR_NOT_WRITABLE` with one
   paragraph that says what to run on the host. No traceback either way.
3. The folder is then restricted to its owner (mode 0700) where the
   filesystem allows it; otherwise that step is skipped with an INFO log.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from sethlans_manager.state_dir import DATA_DIR_ENV, source_data_dir

logger = logging.getLogger("sethlans.container")

#: Missing or invalid ``SETHLANS_MANAGER_DATA_DIR`` (same code as the
#: entrypoint's other configuration errors).
EXIT_DATA_DIR_INVALID = 2
#: The data folder cannot be written (distinct from 1 = start failure
#: and 2 = configuration error).
EXIT_DATA_DIR_NOT_WRITABLE = 3

# Host folder the default compose file mounts on /data.
COMPOSE_DATA_FOLDER = "deploy/docker/data/manager"
DATA_DIR_MODE = 0o700


def _ids() -> tuple:
    """Return (uid, gid) of this process; ``("?", "?")`` off POSIX."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return ("?", "?")
    return (getuid(), getgid())


def _resolve() -> Tuple[Optional[Path], Optional[str]]:
    """Return (data dir, None) or (None, configuration error message)."""
    raw = os.environ.get(DATA_DIR_ENV, "")
    hint = "set it to an absolute path; the image default is /data"
    if not raw.strip():
        return None, (
            f"{DATA_DIR_ENV} is not set; the manager image keeps all of "
            f"its state there ({hint})."
        )
    try:
        # The same resolution every manager reader uses (an absolute
        # path is required by shared.frozen_paths.get_data_dir).
        return source_data_dir("/unused-default"), None
    except ValueError as exc:
        return None, f"{DATA_DIR_ENV} is not usable: {exc} ({hint})."


def _not_writable_message(data_dir: Path, exc: OSError) -> str:
    uid, gid = _ids()
    reason = exc.strerror or exc.__class__.__name__
    return (
        f"The manager cannot write its data folder {data_dir} "
        f"({reason}); it runs as uid {uid}, gid {gid}. If {data_dir} "
        "is a bind mount, Docker probably created the host folder "
        "owned by root. On the host, run "
        f"`sudo chown -R {uid}:{gid} <host folder>` (with the default "
        f"compose file: {COMPOSE_DATA_FOLDER}) and start the container "
        "again, or use a named volume instead of a bind mount."
    )


def restrict_to_owner(data_dir: Path) -> None:
    """Set ``data_dir`` to mode 0700; skip quietly where that fails."""
    try:
        os.chmod(data_dir, DATA_DIR_MODE)
    except OSError as exc:
        logger.info(
            "Left the permissions of %s as they are (chmod 0700: %s)",
            data_dir, exc.strerror or exc.__class__.__name__,
        )


def data_dir_problem() -> Optional[Tuple[int, str]]:
    """Return ``(exit code, one-paragraph message)``, or ``None`` when fine."""
    data_dir, error = _resolve()
    if error:
        return EXIT_DATA_DIR_INVALID, error
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=data_dir):
            pass
    except OSError as exc:
        return EXIT_DATA_DIR_NOT_WRITABLE, _not_writable_message(data_dir, exc)
    restrict_to_owner(data_dir)
    return None
