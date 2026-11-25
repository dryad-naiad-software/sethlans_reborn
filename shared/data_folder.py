# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The data folder's checks and owner-only creation (FR-DF-2), stdlib only.

:func:`check_data_root` lists what is wrong with a proposed data folder
without changing anything (the setup form uses it). :func:`prepare_data_root`
runs the same checks again at the moment it creates or adopts the folder,
so no caller can create a data folder that skipped them:

* the root, ``manager/`` and ``worker/`` are created with
  ``shared.owner_only_acl.create_owner_only_dir`` when missing;
* an existing one must be a folder owned by this process's user
  (``is_owned_by_current_user``); it is made owner-only
  (``apply_owner_only``) unless ``verify_owner_only`` already passes,
  and ``verify_owner_only`` must pass afterwards;
* the marker file ``.sethlans-data`` is written in the root.

Any failure raises :class:`DataFolderError` with a message fit for the
setup form; nothing is taken over half-way silently.

:func:`ensure_configured_data_root` is the run-time creation point for a
data folder chosen in setup (``[paths] data_dir``): the manager calls it
before it creates its own folders under the data folder.
"""
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import List, Optional

from shared import frozen_paths, owner_only_acl

MARKER_NAME = ".sethlans-data"
MARKER_TEXT = b"This folder holds Sethlans data (manager/ and worker/).\n"
COMPONENTS = ("manager", "worker")
LOW_SPACE_BYTES = 20 * 1000 ** 3  # 20 GB: below this the form warns
# Folders the operating system owns: the four FR-DF-2 names; their macOS
# targets (/etc and /var are links into /private, and the resolved form is
# checked too); macOS's own system roots; and the boot, binary and kernel
# trees. /tmp is not here (nor /private/tmp), so neither is its alias.
_POSIX_SYSTEM_PREFIXES = (
    "/usr", "/etc", "/opt", "/var",
    "/private/etc", "/private/var",
    "/System", "/Library", "/Applications",
    "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/boot", "/dev", "/proc", "/sys", "/run",
)


class DataFolderError(Exception):
    """A data folder that must not be used, or could not be made owner-only."""


def install_dir() -> Optional[Path]:
    """The install folder data must stay out of, or ``None`` from source.

    Frozen: the parent of the cross-component ``bin/`` folder
    (``frozen_paths.get_install_root``). From source there is no install,
    and the checkout itself is no system folder, so nothing is excluded.
    """
    if not frozen_paths.is_frozen():
        return None
    return frozen_paths.get_install_root().parent


def _is_under(path: Path, parent: Path) -> bool:
    try:
        return os.path.normcase(os.path.commonpath([str(path), str(parent)])) == os.path.normcase(str(parent))
    except ValueError:  # different drives
        return False


def _windows_system_problem(path: Path) -> Optional[str]:
    parts = path.parts
    if len(parts) < 2:
        return None
    top = parts[1].casefold()
    if top == "windows" or top.startswith("program files"):
        return f"{path} is inside a system folder ({Path(parts[0]) / parts[1]})"
    for env in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(env)
        if value and _is_under(path, Path(value)):
            return f"{path} is inside a system folder ({value})"
    return None


def _system_problem(path: Path) -> Optional[str]:
    if path == Path(path.anchor):
        return f"{path} is the root of a drive or file system"
    if sys.platform == "win32":
        return _windows_system_problem(path)
    for prefix in _POSIX_SYSTEM_PREFIXES:
        if _is_under(path, Path(prefix)):
            return f"{path} is inside a system folder ({prefix})"
    return None


def is_link(path: Path) -> bool:
    """True for a symbolic link, a Windows junction or any other reparse point."""
    if path.is_symlink() or path.is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _forms(path: Path) -> List[Path]:
    """The path as given (normalised) and its resolved form, when they differ."""
    given = Path(os.path.normpath(path))
    resolved = Path(os.path.realpath(given))
    return [given] if resolved == given else [given, resolved]


def check_data_root(path) -> List[str]:
    """Return the problems with ``path`` as a data folder (empty: usable).

    Absolute; not a file-system root; not under ``C:\\Windows``,
    ``C:\\Program Files*`` (or the folders the OS names for them) or a
    POSIX system folder (``_POSIX_SYSTEM_PREFIXES``), as given or
    resolved; not inside the install folder; and, when it exists, a plain
    folder (no symbolic link, junction or other reparse point) owned by
    this process's user. Changes nothing.
    """
    raw = os.fspath(path) if path is not None else ""
    if not raw or not os.path.isabs(raw):
        return ["The data folder must be an absolute path."]
    problems: List[str] = []
    install = install_dir()
    for form in _forms(Path(raw)):
        problem = _system_problem(form)
        if problem is None and install is not None and _is_under(form, install):
            problem = f"{form} is inside the install folder ({install})"
        if problem and problem not in problems:
            problems.append(problem)
    if problems:
        return problems
    target = Path(os.path.normpath(raw))
    if target.exists() or is_link(target):
        if is_link(target) or not target.is_dir():
            return [f"{target} is a link or not a folder; choose a plain folder."]
        if not owner_only_acl.is_owned_by_current_user(target):
            return [f"{target} belongs to another account; choose a folder you own."]
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


def _adopt_dir(folder: Path) -> None:
    """Create ``folder`` owner-only, or check and tighten an existing one."""
    if not folder.exists() and not is_link(folder):
        folder.parent.mkdir(parents=True, exist_ok=True)
        owner_only_acl.create_owner_only_dir(folder)
    if is_link(folder) or not folder.is_dir():
        raise DataFolderError(f"{folder} is a link or not a folder; choose a plain folder.")
    if not owner_only_acl.is_owned_by_current_user(folder):
        raise DataFolderError(f"{folder} belongs to another account; choose a folder you own.")
    if not owner_only_acl.verify_owner_only(folder):
        owner_only_acl.apply_owner_only(folder)
        if not owner_only_acl.verify_owner_only(folder):
            raise DataFolderError(f"Could not limit {folder} to the current user.")


def _write_marker(root: Path) -> None:
    marker = root / MARKER_NAME
    if is_link(marker) or (marker.exists() and not marker.is_file()):
        raise DataFolderError(f"{marker} exists and is not a regular file.")
    if marker.exists():
        return
    try:
        fd = owner_only_acl.create_owner_only_file(marker)
    except FileExistsError:
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
