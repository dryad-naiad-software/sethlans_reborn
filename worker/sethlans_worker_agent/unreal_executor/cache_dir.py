# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``CACHE_DIR`` validity and owner-only ACL (Unreal foundation FR-WC-3, FR-WC-4).

Validity (a violation disables Unreal with the reason; never a crash):

* the absolute path matches ``^[A-Za-z]:\\\\[A-Za-z0-9_.\\-\\\\]*\\Z``, is at
  most 120 characters and is not a drive root: no whitespace, ``"``, ``,``
  or ``;`` (UE splits ``-ExecCmds`` on ``,``) and no UNC path. The check is
  lexical first, so a UNC value never reaches a filesystem call;
* not inside a OneDrive folder (case-insensitive prefix);
* on a fixed local disk (``GetDriveTypeW``, checked before ``realpath`` so
  a mapped network drive is never touched);
* neither it nor any ancestor is a symlink or junction
  (``normcase(realpath(p)) == normcase(p)``), checked before and after
  preparation and again, with owner and ACL, in ``complete_cache_dir``
  under the owner lock (a junction swapped in and back between the
  preparation checks cannot pass).

Creation and adoption (Sec S1). A new directory is created owner-only
**from birth** (``owner_only_acl.create_owner_only_dir``), so no other
account can hold a handle opened under a looser ACL; re-applying a DACL
later would not revoke such handles. An existing directory is **never
taken over**: it is refused unless its owner is the token user, and also
when it has content but no ``.acl-applied`` marker. Its DACL is verified
and, if needed, re-applied once (fail closed).

Trust (Sec S2). ``prepare_cache_dir`` writes nothing but the directory
itself. After the owner lock is taken, ``complete_cache_dir`` writes the
marker only for a directory this process just created, removes a stale
marker from an untrusted one, and runs the write test. Content is
trusted only for a new directory or one with a marker whose ACL verified
without a re-apply. Otherwise project-archive recovery (FR-PA-18) must
clean it and then call ``mark_content_trusted``; until then every
startup reports it untrusted.

Forward requirements for chunk 6 (``project_archive``):

* create the fixed top-level children (``p/``, ``w/``, ``o/``, ``j/``,
  ``downloads/``, ``trash/``, ``index.json``) exclusively (fail if they
  exist after recovery), never by reusing what is there;
* before using any child, check that its owner is the token user and that
  it is not a reparse point (``os.lstat`` + ``FILE_ATTRIBUTE_REPARSE_POINT``);
* on every cache acquire, ``verify_owner_only(CACHE_DIR)`` and reject a
  reparse point anywhere on the entry's path.
"""
import logging
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional, Tuple

from sethlans_worker_agent.unreal_executor import detection
from shared import owner_only_acl

logger = logging.getLogger(__name__)

ALLOWLIST = re.compile(r'^[A-Za-z]:\\[A-Za-z0-9_.\-\\]*\Z', re.ASCII)
_DRIVE_ROOT = re.compile(r'^[A-Za-z]:\\*\Z', re.ASCII)
MAX_LENGTH = 120
ONEDRIVE_VARS = ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')
ACL_MARKER = '.acl-applied'
OWNER_LOCK_NAME = '.owner.lock'
ACL_REASON = "cache dir ACL could not be applied or verified"
OWNER_REASON = "cache dir is owned by another account; remove it or choose another path"
UNMARKED_REASON = "cache dir is not empty and was not created by Sethlans; remove it or choose another path"
LINK_REASON = "invalid SETHLANS_UNREAL_CACHE_DIR, it or a parent folder is a symlink or junction"
_INVALID = "invalid SETHLANS_UNREAL_CACHE_DIR, "


class CacheDirError(ValueError):
    """``CACHE_DIR`` is unusable; the message is the disabled reason."""


@dataclass(frozen=True)
class PreparedCacheDir:
    path: Path
    content_trusted: bool
    created: bool = False


def check_cache_dir_text(path: str, env: Optional[Mapping[str, str]] = None) -> None:
    """Lexical FR-WC-3 rules on an absolute path. Raises ``CacheDirError``."""
    env = os.environ if env is None else env
    if len(path) > MAX_LENGTH:
        raise CacheDirError(_INVALID + f"longer than {MAX_LENGTH} characters")
    if not ALLOWLIST.match(path):
        raise CacheDirError(_INVALID + "it must be a local drive path using only letters, digits, "
                            "'_', '.', '-' (no spaces, quotes, commas or semicolons)")
    if _DRIVE_ROOT.match(path):
        raise CacheDirError(_INVALID + "a drive root is not allowed, use a folder below it")
    folded = os.path.normcase(path).rstrip('\\/').casefold()
    for var in ONEDRIVE_VARS:
        root = (env.get(var) or '').strip().rstrip('\\/').casefold()
        if root and (folded == root or folded.startswith((root + '\\', root + '/'))):
            raise CacheDirError(_INVALID + "it is inside a OneDrive folder")


def _is_link(path) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


def _check_no_links(path: str, env) -> None:
    # Fixed disk first: realpath must never touch a mapped network drive.
    if not detection.is_fixed_drive(path):
        raise CacheDirError(_INVALID + "it must be on a fixed local disk")
    if _is_link(path):
        raise CacheDirError(LINK_REASON)
    resolved = os.path.realpath(path)
    check_cache_dir_text(resolved, env)
    if os.path.normcase(resolved) != os.path.normcase(path):
        raise CacheDirError(LINK_REASON)


def resolve_cache_dir(raw: str, env: Optional[Mapping[str, str]] = None) -> Path:
    """Validate ``raw`` lexically (no filesystem call on failure), then for links."""
    absolute = os.path.abspath(raw or '')
    check_cache_dir_text(absolute, env)
    _check_no_links(absolute, env)
    return Path(absolute)


def ensure_owner_only(path: Path) -> Tuple[bool, bool]:
    """Verify, re-applying once if needed. Returns ``(ok, reapplied)``. Never raises."""
    if owner_only_acl.verify_owner_only(path):
        return True, False
    try:
        owner_only_acl.apply_owner_only(path)
    except (OSError, ValueError) as exc:
        logger.error("Could not apply the owner-only ACL to %s: %s", path, exc)
        return False, True
    return owner_only_acl.verify_owner_only(path), True


def _create(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return owner_only_acl.create_owner_only_dir(path)
    except OSError as exc:
        raise CacheDirError(f"SETHLANS_UNREAL_CACHE_DIR could not be created ({type(exc).__name__})") from exc


def _has_content(path: Path) -> bool:
    with os.scandir(path) as entries:
        return any(entry.name != OWNER_LOCK_NAME for entry in entries)


def _check_existing(path: Path) -> bool:
    """Refuse a directory this worker must not adopt; returns whether it has a marker."""
    if _is_link(path) or not path.is_dir():
        raise CacheDirError(_INVALID + "it is not a directory")
    if not owner_only_acl.is_owned_by_current_user(path):
        raise CacheDirError(OWNER_REASON)
    marker = path / ACL_MARKER
    has_marker = (marker.is_file() and not _is_link(marker)
                  and owner_only_acl.is_owned_by_current_user(marker))
    try:
        if not has_marker and _has_content(path):
            raise CacheDirError(UNMARKED_REASON)
    except OSError as exc:
        raise CacheDirError(f"SETHLANS_UNREAL_CACHE_DIR cannot be listed ({type(exc).__name__})") from exc
    return has_marker


def prepare_cache_dir(raw: str, env: Optional[Mapping[str, str]] = None) -> PreparedCacheDir:
    """Create or check ``CACHE_DIR`` and its ACL. Writes no file. Raises ``CacheDirError``."""
    path = resolve_cache_dir(raw, env)
    created = _create(path)
    has_marker = False if created else _check_existing(path)
    ok, reapplied = ensure_owner_only(path)
    if not ok:
        raise CacheDirError(ACL_REASON)
    _check_no_links(str(path), env)
    trusted = created or (has_marker and not reapplied)
    if not trusted:
        logger.warning("Unreal cache dir %s content is untrusted until recovery cleans it.", path)
    return PreparedCacheDir(path=path, content_trusted=trusted, created=created)


def complete_cache_dir(prepared: PreparedCacheDir) -> None:
    """Marker bookkeeping and write test; call only while holding the owner lock.

    The checks of ``prepare_cache_dir`` follow junctions, so a directory
    swapped for a junction (and back) between them could pass. With the
    owner lock held, its open handle inside the directory prevents a
    rename, so link, owner and ACL are re-checked here before any write.
    """
    _check_no_links(str(prepared.path), None)
    if not owner_only_acl.is_owned_by_current_user(prepared.path):
        raise CacheDirError(OWNER_REASON)
    if not owner_only_acl.verify_owner_only(prepared.path):
        raise CacheDirError(ACL_REASON)
    marker = prepared.path / ACL_MARKER
    try:
        if prepared.created:
            with open(marker, 'x', encoding='ascii') as handle:
                handle.write('created owner-only by Sethlans\n')
        elif not prepared.content_trusted and marker.exists():
            marker.unlink()  # stays untrusted on later startups until recovery
        with tempfile.NamedTemporaryFile(dir=prepared.path, prefix='.write-test-', delete=True):
            pass
    except OSError as exc:
        raise CacheDirError(f"SETHLANS_UNREAL_CACHE_DIR is not writable ({type(exc).__name__})") from exc


def mark_content_trusted(prepared: PreparedCacheDir) -> PreparedCacheDir:
    """Record that recovery cleaned the directory (chunk 6, owner lock held)."""
    if not (owner_only_acl.verify_owner_only(prepared.path)
            and owner_only_acl.is_owned_by_current_user(prepared.path)):
        raise CacheDirError(ACL_REASON)
    (prepared.path / ACL_MARKER).write_text('cleaned by Sethlans recovery\n', encoding='ascii')
    return replace(prepared, content_trusted=True)
