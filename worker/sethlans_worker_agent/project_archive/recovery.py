# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup recovery of ``CACHE_DIR`` (Unreal foundation FR-PA-18, FR-WC-4,
FR-WC-6, Sec N2).

``recover(prepared, limits, keep_job_logs, sweep=...)`` runs once before
the worker polls, only when Unreal is enabled and the cache dir was
prepared (``unreal_executor.cache_dir``). In order:

0. The process must hold the owner lock on ``CACHE_DIR`` (FR-WC-6);
   otherwise nothing is touched and Unreal stays disabled.
1. The orphan sweep (FR-UE-23, supplied by the executor as ``sweep``)
   runs before any deletion.
1a. **Untrusted content** (``prepared.content_trusted`` is False: no
   ``.acl-applied`` marker, or the ACL had to be re-applied): ``trash/``
   is made usable first. An existing ``trash/`` that is a real directory
   owned by the token user is reused (its content is deleted in step 2);
   a link there is removed as a link, and a foreign or non-directory one
   is set aside and an owner-only ``trash/`` created. Then every other
   child (``p/``, ``w/``, ``o/``, ``j/``, ``downloads/``, ``index.json``,
   anything else) is moved into it; the cache starts empty.
2. Each fixed child is created exclusively or checked (owner, no link; a
   bad one is set aside and recreated). Then ``downloads/*``,
   ``p/*.tmp-*``, ``trash/*``, everything under ``w/`` and ``o/``, stale
   ``index.json`` temp files and ``j/*/output`` are deleted (reparse-safe),
   and ``j/`` is pruned to the newest ``KEEP_JOB_LOGS`` job dirs. If a
   ``w/`` or ``j/`` directory cannot be deleted, Unreal is disabled with
   "could not clear Unreal working directories (orphaned process?)".
   After an untrusted start, ``cache_dir.mark_content_trusted`` records
   that the directory is clean.
3. ``index.json`` is reconciled with ``p/`` (``recovery_index``); an
   unindexed entry is never adopted.

The executor must run this once, and successfully, before it reports
``UNREAL`` in its poll parameters.

Failures other than in steps 0 and 2 are logged and never fatal.
"""
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional

from sethlans_worker_agent.project_archive import fs_safe, owner_lock, recovery_index
from sethlans_worker_agent.project_archive.cache import ArchiveCache, CacheLimits, Entry
from sethlans_worker_agent.project_archive.errors import CacheUnavailableError
from sethlans_worker_agent.project_archive.layout import FIXED_DIRS, INDEX_NAME, CacheLayout
from sethlans_worker_agent.unreal_executor import cache_dir

logger = logging.getLogger(__name__)

WORK_REASON = "could not clear Unreal working directories (orphaned process?)"
_KEEP_AT_ROOT = frozenset({owner_lock.LOCK_FILENAME, cache_dir.ACL_MARKER, 'trash'})


@dataclass(frozen=True)
class RecoveryResult:
    cache: Optional[ArchiveCache]
    prepared: cache_dir.PreparedCacheDir
    reason: str = ''


def _set_aside(layout: CacheLayout, path) -> Optional[object]:
    """Remove a link as a link, or rename a foreign directory next to the children."""
    try:
        if fs_safe.is_reparse(fs_safe.lstat(path)):
            fs_safe.remove_tree(path)
            return None
    except FileNotFoundError:
        return None
    aside = layout.root / f".aside-{path.name}-{uuid.uuid4().hex[:12]}"
    os.rename(fs_safe.long_path(path), fs_safe.long_path(aside))
    return aside


def _ensure_child(layout: CacheLayout, name: str, asides: List) -> None:
    path = layout.root / name
    try:
        fs_safe.ensure_owned_dir(path)
        return
    except OSError as exc:
        logger.warning("Unreal cache child %s/ is not usable (%s); replacing it.", name, exc)
    try:
        aside = _set_aside(layout, path)
        if aside is not None:
            asides.append(aside)
        fs_safe.ensure_owned_dir(path)
    except OSError as exc:
        raise CacheUnavailableError(f"Unreal cache child {name}/ could not be recreated: {exc}") from exc


def _prepare_children(layout: CacheLayout) -> None:
    asides: List = []
    _ensure_child(layout, 'trash', asides)  # first: everything else may be moved into it
    for name in FIXED_DIRS:
        if name != 'trash':
            _ensure_child(layout, name, asides)
    for aside in asides:
        moved = fs_safe.move_aside(aside, layout.trash)
        fs_safe.remove_tree(moved or aside)


def _move_untrusted(layout: CacheLayout) -> None:
    """Step 1a: everything except the lock, marker and a fresh ``trash/`` goes to trash."""
    _ensure_child(layout, 'trash', [])  # a foreign trash/ is set aside at the root, moved below
    with os.scandir(layout.root) as listing:
        names = sorted(entry.name for entry in listing if entry.name not in _KEEP_AT_ROOT)
    for name in names:
        path = layout.root / name
        if fs_safe.is_reparse(fs_safe.lstat(path)):
            fs_safe.remove_tree(path)
        elif fs_safe.move_aside(path, layout.trash) is None:
            logger.warning("Could not move untrusted %s to trash.", path)
    logger.warning("Unreal cache dir %s was untrusted; its previous content was moved to trash.", layout.root)


def _clear_children(directory, fatal: bool) -> bool:
    ok = True
    with os.scandir(directory) as listing:
        names = [entry.name for entry in listing]
    for name in names:
        ok = fs_safe.remove_tree(os.path.join(directory, name)) and ok
    if not ok and not fatal:
        logger.warning("Some entries under %s could not be deleted; retried on the next start.", directory)
    return ok


def _clear_jobs(layout: CacheLayout, keep_job_logs: int) -> bool:
    ok = True
    jobs = []
    with os.scandir(layout.j) as listing:
        for entry in listing:
            st = entry.stat(follow_symlinks=False)
            if fs_safe.is_reparse(st) or not entry.is_dir(follow_symlinks=False):
                ok = fs_safe.remove_tree(layout.j / entry.name) and ok  # a link goes as a link
            else:
                jobs.append((st.st_mtime_ns, entry.name))
    for _mtime, name in jobs:
        ok = fs_safe.remove_tree(layout.j / name / 'output') and ok
    prune_job_dirs(layout, keep_job_logs, jobs)
    return ok


def prune_job_dirs(layout: CacheLayout, keep_job_logs: int, jobs=None) -> None:
    """Keep only the newest ``KEEP_JOB_LOGS`` ``j/<job>`` directories (FR-PA-17); never fatal."""
    if jobs is None:
        with os.scandir(layout.j) as listing:
            jobs = [(entry.stat(follow_symlinks=False).st_mtime_ns, entry.name) for entry in listing]
    for _mtime, name in sorted(jobs, reverse=True)[max(keep_job_logs, 0):]:
        if not fs_safe.remove_tree(layout.j / name):
            logger.warning("Could not prune old Unreal job directory %s.", name)


def _clear(layout: CacheLayout, keep_job_logs: int) -> bool:
    """Step 2 deletions; False when a ``w/`` or ``j/`` directory survived."""
    _clear_children(layout.downloads, fatal=False)
    _clear_children(layout.trash, fatal=False)
    _clear_children(layout.o, fatal=False)
    with os.scandir(layout.p) as listing:
        tmp_entries = [entry.name for entry in listing if '.tmp-' in entry.name]
    for name in tmp_entries:
        fs_safe.remove_tree(layout.p / name)
    with os.scandir(layout.root) as listing:
        temps = [entry.name for entry in listing if entry.name.startswith(INDEX_NAME + '.tmp-')]
    for name in temps:
        fs_safe.remove_tree(layout.root / name)
    work_ok = _clear_children(layout.w, fatal=True)
    return _clear_jobs(layout, keep_job_logs) and work_ok


def _reconcile(layout: CacheLayout) -> List[Entry]:
    try:
        return recovery_index.reconcile(layout)
    except Exception:  # noqa: BLE001 - step 3 is never fatal
        logger.exception("Unreal cache index reconciliation failed; starting with an empty index.")
        return []


def _run_sweep(sweep: Optional[Callable[[], object]]) -> None:
    if sweep is None:
        return
    try:
        sweep()
    except Exception:  # noqa: BLE001 - the sweep logs its own failures
        logger.exception("Unreal orphan sweep failed.")


def _clean(layout: CacheLayout, prepared, keep_job_logs: int):
    """Steps 1a and 2; returns ``(prepared, reason)``."""
    try:
        if not prepared.content_trusted:
            _move_untrusted(layout)
        _prepare_children(layout)
        if not _clear(layout, keep_job_logs):
            return prepared, WORK_REASON
        if not prepared.content_trusted:
            prepared = cache_dir.mark_content_trusted(prepared)
    except (CacheUnavailableError, cache_dir.CacheDirError) as exc:
        return prepared, str(exc)
    except OSError as exc:
        logger.exception("Unreal cache recovery failed.")
        return prepared, f"{WORK_REASON} ({type(exc).__name__})"
    return prepared, ''


def recover(prepared: cache_dir.PreparedCacheDir, limits: CacheLimits, *, keep_job_logs: int,
            sweep: Optional[Callable[[], object]] = None, disk_usage: Callable = shutil.disk_usage,
            clock: Callable[[], float] = time.time) -> RecoveryResult:
    """Run FR-PA-18 on ``prepared.path``; see the module docstring. Never raises."""
    layout = CacheLayout(prepared.path)
    if not owner_lock.held_for_process(prepared.path):
        return RecoveryResult(None, prepared, owner_lock.OWNER_LOCK_REASON)
    _run_sweep(sweep)
    prepared, reason = _clean(layout, prepared, keep_job_logs)
    if reason:
        return RecoveryResult(None, prepared, reason)
    cache = ArchiveCache(layout, limits, disk_usage=disk_usage, clock=clock)
    cache.load(_reconcile(layout))
    return RecoveryResult(cache, prepared)
