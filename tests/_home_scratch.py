# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Scratch folders under ``~/.cache/sethlans-tests`` for tests that need a
data folder the setup checks accept on macOS.

There pytest's ``tmp_path`` resolves under ``/private/var``, a system
folder the data-folder checks refuse, so such tests use a unique
``<uuid hex>`` folder in the home folder instead. Nothing outside
``~/.cache/sethlans-tests`` is ever touched.

On POSIX that home is the account's (``pwd``), never ``$HOME``: the
data-folder checks exempt only the account's home, and fixtures such as
``tls_manager`` point ``$HOME`` at ``tmp_path`` before ``data_root`` is
made (#343).

* :func:`scratch_folder` (a generator for fixtures) makes one and, at
  teardown, removes it and the parent when that is left empty;
* :func:`sweep_stale` (once per session) removes ``<uuid hex>`` folders
  older than a day that killed runs left behind.

A cleanup that fails raises :class:`ScratchCleanupError`, so the teardown
(or the session start) fails instead of leaving files behind quietly.
"""
import errno
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Optional

STALE_SECONDS = 24 * 60 * 60
_ENTRY_NAME = re.compile(r"[0-9a-f]{32}")


class ScratchCleanupError(OSError):
    """A scratch folder could not be removed."""


def account_home() -> Path:
    """The account's home folder (``pwd``) on POSIX, whatever ``$HOME`` says; ``Path.home()`` without
    ``pwd`` or without a passwd entry for this uid (as ``shared.data_folder_rules._account``)."""
    try:
        import pwd
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except (ImportError, KeyError):
        return Path.home()


def home_scratch_root() -> Path:
    return account_home() / ".cache" / "sethlans-tests"


def remove_tree(folder: Path) -> None:
    """Remove ``folder``; raise :class:`ScratchCleanupError` when anything is left."""
    errors: List[str] = []
    shutil.rmtree(folder, onexc=lambda _func, path, exc: errors.append(f"{path}: {exc}"))
    if errors or folder.exists() or folder.is_symlink():
        raise ScratchCleanupError(f"Could not remove the test scratch folder {folder}: {'; '.join(errors)}")


def remove_root_if_empty(root: Path) -> None:
    """Remove ``root`` when it is empty; another run's folder in it keeps it."""
    try:
        root.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise ScratchCleanupError(f"Could not remove the test scratch folder {root}: {exc}") from exc


def scratch_folder(root: Optional[Path] = None):
    """Yield a new ``<root>/<uuid hex>`` folder; remove it (and an empty ``root``) afterwards."""
    root = home_scratch_root() if root is None else Path(root)
    folder = root / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        remove_tree(folder)
        remove_root_if_empty(root)


def sweep_stale(root: Optional[Path] = None, *, max_age: float = STALE_SECONDS,
                now: Optional[float] = None) -> List[Path]:
    """Remove ``<uuid hex>`` folders in ``root`` not changed for ``max_age`` seconds.

    Other names, files and links are left alone. Returns what was removed.
    """
    root = home_scratch_root() if root is None else Path(root)
    if not root.is_dir():
        return []
    now = time.time() if now is None else now
    removed = []
    for entry in sorted(root.iterdir()):
        if not _ENTRY_NAME.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if now - entry.stat().st_mtime >= max_age:
            remove_tree(entry)
            removed.append(entry)
    remove_root_if_empty(root)
    return removed
