# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
On-disk layout under ``CACHE_DIR`` (Unreal foundation FR-PA-1)::

    .owner.lock  .acl-applied  index.json
    downloads/<uuid>.zip.part
    p/<key16>/{.complete.json, tree/, warmup/}     p/<key16>.tmp-<uuid>/
    o/<job_id>-<att8>/
    w/<key16><ov8>-s<slot>[-<n>]/<ProjectStem>/
    j/<job_id>-<att8>/
    trash/

``key16`` is the first 16 hex characters of the pinned SHA-256 and is
used **for the path only**; identity is always the full SHA-256.

Fixed children (chunk-4 forward requirement): each is created
exclusively and owner-only (``create_owner_only_dir``); one that already
exists is used only after an ``lstat`` shows no reparse point and its
owner is the token user. ``verify_root`` repeats the owner-only check of
``CACHE_DIR`` itself and also rejects a reparse point, which
``verify_owner_only`` alone (it follows links) would not.
"""
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.project_archive.errors import CacheUnavailableError
from shared import owner_only_acl

FIXED_DIRS = ('p', 'w', 'o', 'j', 'downloads', 'trash')
INDEX_NAME = 'index.json'
COMPLETE_NAME = '.complete.json'
TREE_NAME = 'tree'
NO_OVERLAY_OV8 = '0' * 8
KEY16_RE = re.compile(r'^[0-9a-f]{16}\Z')
ACL_REASON = "cache dir ACL could not be applied or verified"


def key16(sha256: str) -> str:
    return sha256[:16]


@dataclass(frozen=True)
class CacheLayout:
    root: Path

    @property
    def p(self) -> Path:
        return self.root / 'p'

    @property
    def w(self) -> Path:
        return self.root / 'w'

    @property
    def o(self) -> Path:
        return self.root / 'o'

    @property
    def j(self) -> Path:
        return self.root / 'j'

    @property
    def downloads(self) -> Path:
        return self.root / 'downloads'

    @property
    def trash(self) -> Path:
        return self.root / 'trash'

    @property
    def index(self) -> Path:
        return self.root / INDEX_NAME

    def entry_dir(self, sha256: str) -> Path:
        return self.p / key16(sha256)

    def new_tmp_entry_dir(self, sha256: str) -> Path:
        return self.p / f"{key16(sha256)}.tmp-{uuid.uuid4().hex}"

    def new_part_path(self) -> Path:
        return self.downloads / f"{uuid.uuid4().hex}.zip.part"

    def overlay_dir(self, job_id: int, att8: str) -> Path:
        return self.o / f"{int(job_id)}-{att8}"

    def job_dir(self, job_id: int, att8: str) -> Path:
        return self.j / f"{int(job_id)}-{att8}"

    def work_base_name(self, sha256: str, overlay_sha256: str, slot: int) -> str:
        ov8 = overlay_sha256[:8] if overlay_sha256 else NO_OVERLAY_OV8
        return f"{key16(sha256)}{ov8}-s{int(slot)}"

    def ensure_children(self) -> None:
        """Create each fixed child exclusively, or check the existing one (owner, no link)."""
        for name in FIXED_DIRS:
            try:
                fs_safe.ensure_owned_dir(self.root / name)
            except OSError as exc:
                raise CacheUnavailableError(f"Unreal cache child {name}/ is unusable: {exc}") from exc

    def check_child(self, name: str) -> Path:
        """The fixed child ``name`` after an owner and reparse check."""
        path = self.root / name
        try:
            fs_safe.check_owned_dir(path)
        except OSError as exc:
            raise CacheUnavailableError(f"Unreal cache child {name}/ is unusable: {exc}") from exc
        return path

    def verify_root(self) -> None:
        """Owner-only ACL and owner of ``CACHE_DIR``, and no reparse point (FR-WC-4)."""
        try:
            st = fs_safe.lstat(self.root)
        except OSError as exc:
            raise CacheUnavailableError(ACL_REASON) from exc
        if fs_safe.is_reparse(st) or not owner_only_acl.verify_owner_only(self.root):
            raise CacheUnavailableError(ACL_REASON)


def safe_job_token(att8: str) -> str:
    """Validate a claim-attempt token (8 lowercase hex, FR-PA-1)."""
    if not re.fullmatch(r'[0-9a-f]{8}', att8 or ''):
        raise ValueError("attempt token must be 8 lowercase hex characters")
    return att8


def mkdir_plain(path) -> None:
    """``mkdir`` (parents too) that inherits the cache DACL; long-path safe."""
    os.makedirs(fs_safe.long_path(path), exist_ok=True)
