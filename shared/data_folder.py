# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The data folder's checks and owner-only creation (FR-DF-2), stdlib only.

:func:`check_data_root` lists what is wrong with a proposed data folder
without changing anything (the setup form uses it). :func:`prepare_data_root`
runs the same checks again at the moment it creates or adopts the folder,
so no caller can create a data folder that skipped them:

* where it may go, and what its parent folders must be, is in
  ``shared.data_folder_rules`` (system folders, the install folder,
  parents owned by this user or the system, no planted links);
* missing parent folders are created one level at a time with
  ``shared.owner_only_acl.create_owner_only_dir``, each checked again for
  a link or a foreign owner, so none inherits a loose drive-root ACL;
* the root, ``manager/`` and ``worker/`` are created the same way when
  missing; an existing one must be a plain folder owned by exactly this
  process's user (``is_owned_by_token_user``: an elevated run does not
  take over an Administrators-owned folder). It is made owner-only unless
  it already is: Windows ``apply_owner_only`` and ``verify_owner_only``;
  POSIX ``fchmod`` on a descriptor opened with ``O_NOFOLLOW|O_DIRECTORY``,
  so a folder swapped for a link after the check is refused, not followed;
* the marker file ``.sethlans-data`` is written in the root.

Any failure raises :class:`DataFolderError` with a message fit for the
setup form; nothing is taken over half-way silently.

:func:`ensure_configured_data_root` is the run-time creation point for a
data folder chosen in setup (``[paths] data_dir``): the manager calls it
before it creates its own folders under the data folder.
"""
import errno
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import List, Optional

from shared import frozen_paths, owner_only_acl
from shared.data_folder_rules import (  # noqa: F401 - re-exported API
    DataFolderError,
    install_dir,
    is_link,
    link_message,
    location_problems,
    parent_problem,
    parent_problems,
)

MARKER_NAME = ".sethlans-data"
MARKER_TEXT = b"This folder holds Sethlans data (manager/ and worker/).\n"
COMPONENTS = ("manager", "worker")
LOW_SPACE_BYTES = 20 * 1000 ** 3  # 20 GB: below this the form warns


def _not_plain(folder: Path) -> str:
    return f"{folder} is a link or not a folder; choose a plain folder."


def _not_mine(folder: Path) -> str:
    return f"{folder} belongs to another account; choose a folder you own."


def check_data_root(path) -> List[str]:
    """Return the problems with ``path`` as a data folder (empty: usable).

    Absolute; not a file-system root; not under ``C:\\Windows``,
    ``C:\\Program Files*`` (or the folders the OS names for them) or a
    POSIX system folder, as given or resolved; not inside the install
    folder (``data_folder_rules.location_problems``); existing parents
    owned by this user or the system and free of planted links
    (``parent_problems``); and, when it exists, a plain folder (no symbolic
    link, junction or other reparse point) owned by exactly this process's
    user. Changes nothing.
    """
    raw = os.fspath(path) if path is not None else ""
    if not raw or not os.path.isabs(raw):
        return ["The data folder must be an absolute path."]
    problems = location_problems(raw)
    if problems:
        return problems
    target = Path(os.path.normpath(raw))
    problems = parent_problems(target)
    if problems:
        return problems
    if target.exists() or is_link(target):
        if is_link(target) or not target.is_dir():
            return [_not_plain(target)]
        if not owner_only_acl.is_owned_by_token_user(target):
            return [_not_mine(target)]
    return []


def free_bytes(path) -> Optional[int]:
    """Free space on the volume of ``path`` (its nearest existing parent), or ``None``."""
    probe = Path(os.path.normpath(os.fspath(path)))
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _make_parents(folder: Path) -> None:
    """Create the missing parents of ``folder`` owner-only, top down, one level at a time."""
    missing = []
    level = folder.parent
    while not (level.exists() or is_link(level)) and level.parent != level:
        missing.append(level)
        level = level.parent
    for level in reversed(missing):
        created = owner_only_acl.create_owner_only_dir(level)
        if is_link(level) or not level.is_dir():
            raise DataFolderError(link_message(level))
        problem = None if created else parent_problem(level)  # someone else made it first
        if problem:
            raise DataFolderError(problem)


def _adopt_posix(folder: Path) -> None:
    """Check and tighten ``folder`` through a descriptor that never follows a link."""
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):  # EMLINK: FreeBSD's O_NOFOLLOW
            raise DataFolderError(_not_plain(folder)) from None
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise DataFolderError(_not_plain(folder))
        if info.st_uid != os.geteuid():
            raise DataFolderError(_not_mine(folder))
        if stat.S_IMODE(info.st_mode) & 0o077:
            os.fchmod(fd, 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                raise DataFolderError(f"Could not limit {folder} to the current user.")
    finally:
        os.close(fd)


def _adopt_dir(folder: Path) -> None:
    """Create ``folder`` owner-only, or check and tighten an existing one."""
    if not folder.exists() and not is_link(folder):
        _make_parents(folder)
        owner_only_acl.create_owner_only_dir(folder)
    if sys.platform != "win32":
        _adopt_posix(folder)
        return
    if is_link(folder) or not folder.is_dir():
        raise DataFolderError(_not_plain(folder))
    if not owner_only_acl.is_owned_by_token_user(folder):
        raise DataFolderError(_not_mine(folder))
    if not owner_only_acl.verify_owner_only(folder):
        owner_only_acl.apply_owner_only(folder)
        if not owner_only_acl.verify_owner_only(folder):
            raise DataFolderError(f"Could not limit {folder} to the current user.")


def _write_marker(root: Path) -> None:
    marker = root / MARKER_NAME
    message = f"{marker} exists and is not a regular file."
    if is_link(marker) or (marker.exists() and not marker.is_file()):
        raise DataFolderError(message)
    if marker.exists():
        return
    try:
        fd = owner_only_acl.create_owner_only_file(marker)
    except FileExistsError:
        # Another process won the race: accept only a plain file.
        if is_link(marker) or not marker.is_file():
            raise DataFolderError(message) from None
        return
    try:
        os.write(fd, MARKER_TEXT)
    finally:
        os.close(fd)


def prepare_data_root(path) -> Path:
    """Check ``path`` again, then create or adopt it owner-only (module doc).

    Returns the normalised root. Raises :class:`DataFolderError`.
    """
    problems = check_data_root(path)
    if problems:
        raise DataFolderError(problems[0])
    root = Path(os.path.normpath(os.fspath(path)))
    try:
        _adopt_dir(root)
        for component in COMPONENTS:
            _adopt_dir(root / component)
        _write_marker(root)
    except DataFolderError:
        raise
    except OSError as exc:
        raise DataFolderError(f"Could not prepare {root}: {exc.strerror or type(exc).__name__}") from None
    return root


def ensure_configured_data_root(component: str) -> Optional[Path]:
    """Prepare ``[paths] data_dir`` when it decides where ``component``'s data goes.

    A no-op (``None``) when no data folder is configured, or when
    ``SETHLANS_DATA_ROOT`` or ``SETHLANS_{COMPONENT}_DATA_DIR`` overrides
    it (an operator's or Docker's choice, outside setup). Raises
    :class:`DataFolderError` (or ``ValueError`` for a relative path).
    """
    if frozen_paths.env_path(f"SETHLANS_{component.upper()}_DATA_DIR") is not None:
        return None
    if frozen_paths.env_path(frozen_paths.DATA_ROOT_ENV) is not None:
        return None
    from shared import settings_file  # late: settings_file imports frozen_paths
    configured = settings_file.configured_data_root()
    if configured is None:
        return None
    return prepare_data_root(configured)
