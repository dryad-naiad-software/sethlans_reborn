# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Owner-only directory permissions, stdlib only (Unreal foundation FR-WC-4).

Windows: a **protected** DACL with exactly two inheritable full-access
ACEs, the process token's user and LocalSystem, and the token user as the
directory's **owner** (an owner keeps the implicit right to rewrite the
DACL, so another local user who created the directory could otherwise
restore their access). SDDL::

    O:<user-sid>D:P(A;OICI;FA;;;<user-sid>)(A;OICI;FA;;;SY)

The user SID always comes from the process token (``OpenProcessToken`` ->
``GetTokenInformation(TokenUser)``), never from ``USERNAME``. Everything
goes through ``ctypes``; ``pywin32`` is not used (it is not installed, see
GitHub issue #214, and NF-4 forbids adding it).

POSIX: ``chmod 0700``; verified as no group/other bits and owned by the
effective uid.

``create_owner_only_dir`` creates a directory with that descriptor from
birth (``CreateDirectoryW`` + ``SECURITY_ATTRIBUTES``), so no other account
can open a handle in between; an existing directory is never taken over
(check ``is_owned_by_current_user``). Re-applying a DACL does not revoke
handles opened earlier.

The ctypes calls live in ``shared.owner_only_acl_win``, which loads
``ctypes.wintypes`` and every DLL inside functions, so both modules
import cleanly on every platform. Callers fail closed: any
``OSError`` from ``apply_owner_only`` means "not applied", and
``verify_owner_only`` returns False on any error.

Not related to ``shared/file_acls.py`` (owned by #214, untouched).
"""
import os
import stat
import sys

from shared import owner_only_acl_win as _win

SYSTEM_SID = 'S-1-5-18'
_ACCESS_ALLOWED_ACE_TYPE = 0
_REQUIRED_ACE_FLAGS = 0x1 | 0x2  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE


# Patch surfaces for tests (the ctypes layer itself lives in owner_only_acl_win).
def _win_token_user_sid() -> str:
    return _win.token_user_sid()


def _win_token_owner_sid() -> str:
    return _win.token_owner_sid()


def _win_create_dir(path: str, sddl: str) -> bool:
    return _win.create_dir(path, sddl)


def _win_set_security(path: str, sddl: str) -> None:
    _win.set_security(path, sddl)


def _win_read_security(path: str):
    return _win.read_security(path)


def _is_windows() -> bool:
    return sys.platform == 'win32'


def owner_only_sddl(user_sid: str) -> str:
    """The SDDL ``apply_owner_only`` writes for ``user_sid``."""
    aces = f"(A;OICI;FA;;;{user_sid})"
    if user_sid != SYSTEM_SID:
        aces += "(A;OICI;FA;;;SY)"
    return f"O:{user_sid}D:P{aces}"


def current_user_sid() -> str:
    """SID string of the process token's user (Windows only)."""
    if not _is_windows():
        raise OSError("current_user_sid is only available on Windows")
    return _win_token_user_sid()


def apply_owner_only(path) -> None:
    """Restrict ``path`` to its owner (FR-WC-4). Raises ``OSError`` on failure."""
    if _is_windows():
        _win_set_security(os.fspath(path), owner_only_sddl(current_user_sid()))
    else:
        os.chmod(path, 0o700)


def create_owner_only_dir(path) -> bool:
    """Create ``path`` owner-only from birth (no window with a looser ACL).

    The parent must exist. Returns True when this call created it, False
    when it already existed (it is then left untouched: the caller must
    check its owner and never take it over). Raises ``OSError`` otherwise.
    """
    if _is_windows():
        return _win_create_dir(os.fspath(path), owner_only_sddl(current_user_sid()))
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        return False
    os.chmod(path, 0o700)  # the umask can only remove bits; make it exact
    return True


def current_owner_sids() -> frozenset:
    """SIDs that own objects this process creates (Windows only).

    The token user, plus the token's default owner (``TokenOwner``). For a
    process elevated as a member of Administrators the default owner is
    ``BUILTIN\\Administrators`` (S-1-5-32-544), so a plain ``mkdir`` gets
    that owner (GitHub #228). Such an owner is a privileged account that
    could take ownership anyway.
    """
    if not _is_windows():
        raise OSError("current_owner_sids is only available on Windows")
    sids = {current_user_sid()}
    try:
        sids.add(_win_token_owner_sid())
    except Exception:  # noqa: BLE001 - fall back to the user only (stricter)
        pass
    return frozenset(sids)


def is_owned_by_current_user(path) -> bool:
    """True when ``path``'s owner is this process (POSIX: the effective uid).

    On Windows the owner must be the token user **or** the token's default
    owner (``current_owner_sids``). This is the only owner comparison the
    callers use; ``verify_owner_only`` stays exact (owner == token user),
    and ``create_owner_only_dir`` / ``apply_owner_only`` set that owner
    explicitly.
    """
    try:
        if _is_windows():
            return _win_read_security(os.fspath(path))[1] in current_owner_sids()
        return os.lstat(path).st_uid == os.geteuid()
    except (OSError, ValueError):
        return False


def _acl_is_owner_only(user_sid: str, protected: bool, owner: str, aces) -> bool:
    expected = {user_sid, SYSTEM_SID}
    if not protected or owner != user_sid or aces is None or len(aces) != len(expected):
        return False
    for ace_type, ace_flags, sid in aces:
        if ace_type != _ACCESS_ALLOWED_ACE_TYPE or ace_flags != _REQUIRED_ACE_FLAGS:
            return False
        if sid not in expected:
            return False
    return {sid for _type, _flags, sid in aces} == expected


def verify_owner_only(path) -> bool:
    """True only when ``path`` is owner-only as ``apply_owner_only`` leaves it."""
    try:
        if _is_windows():
            user_sid = current_user_sid()
            protected, owner, aces = _win_read_security(os.fspath(path))
            return _acl_is_owner_only(user_sid, protected, owner, aces)
        st = os.stat(path)
        return stat.S_IMODE(st.st_mode) & 0o077 == 0 and st.st_uid == os.geteuid()
    except (OSError, ValueError):
        return False
