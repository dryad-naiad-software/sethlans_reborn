# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the data-folder tests.

GitHub's Windows runners run elevated: a plain ``mkdir`` (and pytest's own
``tmp_path``) is then owned by ``BUILTIN\\Administrators``, which the exact
owner check (``is_owned_by_token_user``) refuses. A folder a test needs
accepted as an existing data folder is therefore made with
:func:`own_dir`; ``tmp_path`` itself is only ever a parent, where
Administrators counts as the system.
"""
import os
import sys
from pathlib import Path

import pytest

from shared import owner_only_acl

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows owners, ACLs and junctions")
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX uids, mode bits and symlinks")


def own_dir(path: Path) -> Path:
    """``path`` created owner-only and owned by this user (parents made plainly)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    assert owner_only_acl.create_owner_only_dir(path) is True
    return path


def make_link(link: Path, target: Path) -> Path:
    """A folder link that needs no privilege: a junction on Windows, a symlink elsewhere."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)
    return link


def override_owner(mocker, name: str, answers: dict):
    """Patch ``owner_only_acl.<name>`` to give ``answers[path]`` for listed paths, the real answer otherwise."""
    real = getattr(owner_only_acl, name)
    fixed = {os.path.normcase(str(path)): value for path, value in answers.items()}

    def check(path):
        key = os.path.normcase(os.fspath(path))
        return fixed[key] if key in fixed else real(path)

    return mocker.patch.object(owner_only_acl, name, side_effect=check)


def windows_owner(mocker, path: Path, sid: str):
    """Make Windows report ``sid`` as the owner of ``path`` (every other path is read for real)."""
    real = owner_only_acl._win_read_security
    key = os.path.normcase(str(path))

    def read(raw):
        protected, owner, aces = real(raw)
        return (protected, sid, aces) if os.path.normcase(raw) == key else (protected, owner, aces)

    return mocker.patch.object(owner_only_acl, "_win_read_security", side_effect=read)
