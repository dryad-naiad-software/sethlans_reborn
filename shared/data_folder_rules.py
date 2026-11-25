# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Where a data folder may go (FR-DF-2), stdlib only; used by ``shared.data_folder``.

* :func:`location_problems`: not a file-system root, not a system folder,
  not inside the install folder; the path as given and resolved.
* :func:`parent_problems`: every existing parent (the root of the drive
  or file system aside) is owned by this user or the operating system, and
  none is a link or junction unless the operating system owns it (POSIX
  root, e.g. Fedora Atomic's ``/home`` -> ``var/home`` or macOS's ``/var``;
  Windows SYSTEM, Administrators or TrustedInstaller, e.g.
  ``C:\\Documents and Settings``).

On POSIX a path inside the user's home folder (the account's ``pw_dir``,
never ``$HOME``; as given or resolved) or under ``/run/media/<user>/`` (udisks2 mounts) skips the
system-folder prefixes, so ``/var/home/<user>`` (Fedora Atomic) is usable;
the rest of ``/var`` and ``/run`` stays refused.
"""
import os
import stat
import sys
from pathlib import Path
from typing import List, Optional

from shared import frozen_paths, owner_only_acl

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
_RUN_MEDIA = Path("/run/media")


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


def is_under(path: Path, parent: Path) -> bool:
    """True when ``path`` is ``parent`` or inside it (case rules of the OS)."""
    try:
        return os.path.normcase(os.path.commonpath([str(path), str(parent)])) == os.path.normcase(str(parent))
    except ValueError:  # different drives
        return False


def is_link(path: Path) -> bool:
    """True for a symbolic link, a Windows junction or any other reparse point."""
    if path.is_symlink() or path.is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def forms(path: Path) -> List[Path]:
    """The path as given (normalised) and its resolved form, when they differ."""
    given = Path(os.path.normpath(path))
    resolved = Path(os.path.realpath(given))
    return [given] if resolved == given else [given, resolved]


def _windows_system_problem(path: Path) -> Optional[str]:
    parts = path.parts
    if len(parts) < 2:
        return None
    top = parts[1].casefold()
    if top == "windows" or top.startswith("program files"):
        return f"{path} is inside a system folder ({Path(parts[0]) / parts[1]})"
    for env in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(env)
        if value and is_under(path, Path(value)):
            return f"{path} is inside a system folder ({value})"
    return None


def _account():
    """This process's password-database entry (effective uid), or ``None``."""
    try:
        import pwd

        return pwd.getpwuid(os.geteuid())
    except (ImportError, KeyError, OSError):
        return None


def _is_own_folder(path: Path) -> bool:
    """An existing folder (links followed) owned by the effective uid."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()


def _user_folders() -> List[Path]:
    """The POSIX home folder that skips the system prefixes, as given and resolved.

    From the account's ``pw_dir``, never ``$HOME`` (a system account with
    ``HOME=/var/lib`` must not exempt ``/var/lib``); only when it exists,
    is owned by the effective uid and the given form is at least two levels
    deep, so a home of ``/`` or ``/var`` exempts nothing. A form (given or
    resolved) that is a system prefix or a parent of one is dropped too
    (macOS: ``/var`` resolves to ``/private/var``, three parts). Fedora
    Atomic: ``/var/home/<user>``, or ``/home/<user>`` with ``/home`` ->
    ``var/home``.
    """
    entry = _account()
    pw_dir = getattr(entry, "pw_dir", None)
    if not pw_dir or not os.path.isabs(pw_dir):
        return []
    home = Path(os.path.normpath(pw_dir))
    if len(home.parts) < 3 or not _is_own_folder(home):  # depth of the given form, before resolving
        return []
    return [form for form in forms(home) if len(form.parts) >= 3 and not _covers_a_prefix(form)]


def _covers_a_prefix(folder: Path) -> bool:
    """True when ``folder`` is a system prefix or a parent of one."""
    return any(is_under(Path(prefix), folder) for prefix in _POSIX_SYSTEM_PREFIXES)


def _run_media_user() -> Optional[Path]:
    name = getattr(_account(), "pw_name", None)
    return _RUN_MEDIA / name if name else None


def _posix_exempt(path: Path) -> bool:
    if any(is_under(path, home) for home in _user_folders()):
        return True
    media = _run_media_user()
    return media is not None and path != media and is_under(path, media)


def _system_problem(path: Path) -> Optional[str]:
    if path == Path(path.anchor):
        return f"{path} is the root of a drive or file system"
    if sys.platform == "win32":
        return _windows_system_problem(path)
    if _posix_exempt(path):
        return None
    for prefix in _POSIX_SYSTEM_PREFIXES:
        if is_under(path, Path(prefix)):
            return f"{path} is inside a system folder ({prefix})"
    return None


def location_problems(raw: str) -> List[str]:
    """Root, system-folder and install-folder problems of ``raw`` (absolute)."""
    problems: List[str] = []
    install = install_dir()
    for form in forms(Path(raw)):
        problem = _system_problem(form)
        if problem is None and install is not None and is_under(form, install):
            problem = f"{form} is inside the install folder ({install})"
        if problem and problem not in problems:
            problems.append(problem)
    return problems


def link_message(path: Path) -> str:
    return f"{path} is a link or junction above the data folder; give the folder's real path."


def owner_message(path: Path) -> str:
    return f"{path} belongs to another account; choose a data folder whose parent folders you or the system own."


def parent_problem(parent: Path) -> Optional[str]:
    """What is wrong with one existing parent folder, or ``None``."""
    if is_link(parent):
        return None if owner_only_acl.is_owned_by_system(parent) else link_message(parent)
    if not owner_only_acl.is_owned_by_user_or_system(parent):
        return owner_message(parent)
    return None


def parent_problems(path: Path) -> List[str]:
    """Check the existing parents of ``path`` (as given and resolved), top down."""
    problems: List[str] = []
    for form in forms(path):
        for parent in reversed(form.parents):
            if parent == Path(parent.anchor) or not (parent.exists() or is_link(parent)):
                continue
            problem = parent_problem(parent)
            if problem and problem not in problems:
                problems.append(problem)
                break
    return problems
