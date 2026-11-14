# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job working copy (Unreal foundation FR-PA-13, FR-PA-14, FR-PA-15,
FR-PA-19, FR-PA-20).

``materialize`` builds ``w/<key16><ov8>-s<slot>/<ProjectStem>/``:

1. An existing directory at that path is renamed to ``trash/`` and
   deleted. If the rename fails (files still open), the first ``-<n>``
   suffix (n = 1..9) that can be used is taken, a WARNING is logged and the
   old path is queued for clearing by later jobs; with none left the job
   fails ("Cannot clear Unreal working copy; ...").
2. The relpaths are resolved **before** anything is written: the pristine
   project-folder files from a reparse-safe walk of ``tree/`` (never the
   editable ``.complete.json``), united with the overlay files. The project
   folder is the parent of ``uproject_path``, which must name a file in
   the tree (exact match first, then case-insensitive; FR-PA-20).
3. Path-length guard: ``len(root) + 1 + max(len(relpath))`` above 250
   fails unless Windows long paths are enabled.
4. Per relpath: an overlay file is **copied** from the staged overlay
   (it replaces the pristine file of the same name, compared
   case-insensitively; an overlay never deletes); a ``COPY_GLOBS`` match
   (``*.uproject`` at the top, ``Config/**``, and the directories UE
   rewrites in place: ``Saved/**``, ``Intermediate/**``,
   ``DerivedDataCache/**``, ``Plugins/**/Config/**``) is copied from pristine;
   everything else is hard-linked in ``hardlink`` mode, else copied.
   Copied bytes are reserved first (FR-PA-11). If ``os.link`` fails, the
   rest is copied (its bytes reserved first), a WARNING is logged once and
   the mode reads ``copy(fallback)`` (FR-PA-14).

A hard-linked path is never opened for writing by the worker.
"""
import logging
import os
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from sethlans_worker_agent.project_archive import fs_safe, zip_rules
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.errors import UprojectNotFoundError, WorkingCopyError
from sethlans_worker_agent.project_archive.layout import CacheLayout

logger = logging.getLogger(__name__)

COPY_GLOBS = ('*.uproject', 'Config/**', 'Saved/**', 'Intermediate/**', 'DerivedDataCache/**',
              'Plugins/**/Config/**')
COPY_TOP_DIRS = frozenset({'config', 'saved', 'intermediate', 'deriveddatacache'})
PATH_LIMIT = 250
MAX_SUFFIX = 9
CHUNK = 1024 * 1024
STUCK_MESSAGE = "Cannot clear Unreal working copy; orphaned process may hold files."
_stale: Set[Path] = set()
_stale_lock = threading.Lock()  # leaf lock


@dataclass(frozen=True)
class WorkingCopy:
    root: Path
    project_dir: Path
    uproject: Path
    mode: str  # 'hardlink' | 'copy' | 'copy(fallback)'
    files: int
    seconds: float


@dataclass(frozen=True)
class _Plan:
    stem: str
    sources: Dict[str, Path]  # project relpath -> file to link or copy
    forced_copies: Set[str]   # overlay files
    dirs: List[str]


def long_paths_enabled() -> bool:
    """``HKLM\\...\\FileSystem\\LongPathsEnabled == 1``; True off Windows."""
    if sys.platform != 'win32':
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return winreg.QueryValueEx(key, 'LongPathsEnabled')[0] == 1
    except OSError:
        return False


def find_uproject(files, uproject: str) -> str:
    """The tree relpath of ``uproject`` (exact, then case-insensitive; FR-PA-20)."""
    wanted = uproject.replace('\\', '/')
    if not wanted.lower().endswith('.uproject'):
        raise UprojectNotFoundError(uproject)
    if wanted in files:
        return wanted
    key = zip_rules.fold_key(wanted)
    matches = [rel for rel in files if zip_rules.fold_key(rel) == key]
    if len(matches) != 1:
        raise UprojectNotFoundError(uproject)
    return matches[0]


def is_copy_glob(relpath: str) -> bool:
    """``COPY_GLOBS``: files UE may rewrite in place, so never hard-linked (Sec S7).

    The top-level ``*.uproject``; everything under the top-level ``Config/``,
    ``Saved/``, ``Intermediate/`` and ``DerivedDataCache/``; and any
    ``Config/`` directory inside ``Plugins/``.
    """
    parts = [part.lower() for part in relpath.split('/')]
    if len(parts) == 1:
        return parts[0].endswith('.uproject')
    if parts[0] in COPY_TOP_DIRS:
        return True
    return parts[0] == 'plugins' and 'config' in parts[1:-1]


def _under(prefix: str, relpaths) -> List[str]:
    if not prefix:
        return list(relpaths)
    start = prefix + '/'
    return [rel[len(start):] for rel in relpaths if rel.startswith(start)]


def _plan(tree: Path, uproject: str, overlay) -> _Plan:
    listing = fs_safe.walk_tree(tree)
    found = find_uproject(listing.files, uproject)
    prefix = found.rsplit('/', 1)[0] if '/' in found else ''
    base = tree.joinpath(*prefix.split('/')) if prefix else tree
    pristine = _under(prefix, listing.files)
    dirs = _under(prefix, listing.dirs)
    sources = {rel: base.joinpath(*rel.split('/')) for rel in pristine}
    by_key = {zip_rules.fold_key(rel): rel for rel in pristine}
    dir_keys = {zip_rules.fold_key(d) for d in dirs}
    forced: Set[str] = set()
    for rel in (overlay.files if overlay is not None else ()):
        key = zip_rules.fold_key(rel)
        if key in dir_keys or any(key[:n] in by_key for n in range(1, len(key))):
            raise WorkingCopyError(f"Overlay file {rel} conflicts with a project directory or file.")
        target = by_key.get(key, rel)
        sources[target] = overlay.path.joinpath(*rel.split('/'))
        forced.add(target)
    stem = found.rsplit('/', 1)[-1][:-len('.uproject')]
    return _Plan(stem=stem, sources=sources, forced_copies=forced, dirs=dirs)


def _retry_stale() -> None:
    with _stale_lock:
        stale = list(_stale)
    for path in stale:
        if fs_safe.remove_tree(path):
            with _stale_lock:
                _stale.discard(path)


def _unstale(path: Path) -> None:
    with _stale_lock:
        _stale.discard(path)


def _choose_root(layout: CacheLayout, name: str) -> Path:
    _retry_stale()
    candidates = [layout.w / name] + [layout.w / f"{name}-{n}" for n in range(1, MAX_SUFFIX + 1)]
    for index, candidate in enumerate(candidates):
        if not fs_safe.lexists(candidate):
            _unstale(candidate)
            return candidate
        moved = fs_safe.move_aside(candidate, layout.trash)
        if moved is not None:
            fs_safe.remove_tree(moved)
            _unstale(candidate)  # Conc C4: a queued clear must never hit the new working copy
            return candidate
        with _stale_lock:
            _stale.add(candidate)
        if index == 0:
            logger.warning("Could not clear Unreal working copy %s; using a suffixed path.", candidate)
    raise WorkingCopyError(STUCK_MESSAGE)


def _mkdirs(path: Path) -> None:
    os.makedirs(fs_safe.long_path(path), exist_ok=True)


def _copy_file(src: Path, dst: Path, abort: Abort, on_bytes) -> None:
    with open(fs_safe.long_path(src), 'rb') as handle, open(fs_safe.long_path(dst), 'xb') as out:
        while True:
            abort.check()
            data = handle.read(CHUNK)
            if not data:
                return
            out.write(data)
            on_bytes(len(data))


def _bytes(plan: _Plan, rels) -> int:
    return sum(fs_safe.lstat(plan.sources[rel]).st_size for rel in rels)


def _write(plan: _Plan, project: Path, mode: str, reserve, abort: Abort, link) -> str:
    order = sorted(plan.sources)
    copies = {rel for rel in order if rel in plan.forced_copies or is_copy_glob(rel) or mode != 'hardlink'}
    linking, used = mode == 'hardlink', ('hardlink' if mode == 'hardlink' else 'copy')
    with ExitStack() as stack:
        planned = stack.enter_context(reserve(_bytes(plan, copies)))
        fallback = None
        for index, rel in enumerate(order):
            abort.check()
            dst = project.joinpath(*rel.split('/'))
            _mkdirs(dst.parent)
            if rel in copies:
                _copy_file(plan.sources[rel], dst, abort, planned.consume)
                continue
            if linking:
                try:
                    link(fs_safe.long_path(plan.sources[rel]), fs_safe.long_path(dst))
                    continue
                except OSError as exc:
                    logger.warning("Hard links failed in the Unreal working copy (%s); copying instead.", exc)
                    linking, used = False, 'copy(fallback)'
                    rest = [r for r in order[index:] if r not in copies]
                    fallback = stack.enter_context(reserve(_bytes(plan, rest)))
            _copy_file(plan.sources[rel], dst, abort, (fallback or planned).consume)
    return used


def materialize(layout: CacheLayout, tree: Path, uproject: str, overlay=None, *, sha256: str,
                overlay_sha256: str = '', slot: int = 0, mode: str = 'hardlink',
                reserve: Callable[[int], object], abort: Abort = NEVER,
                long_paths: Callable[[], bool] = long_paths_enabled,
                link: Callable = os.link) -> WorkingCopy:
    """Build the job's working copy; see the module docstring."""
    started = time.monotonic()
    plan = _plan(Path(tree), uproject, overlay)
    root = _choose_root(layout, layout.work_base_name(sha256, overlay_sha256, slot))
    project = root / plan.stem
    longest = len(str(project)) + 1 + max((len(rel) for rel in plan.sources), default=0)
    if longest > PATH_LIMIT and not long_paths():
        raise WorkingCopyError(f"Unreal working copy path would exceed MAX_PATH ({longest} chars); "
                               "set SETHLANS_UNREAL_CACHE_DIR to a shorter path.")
    _mkdirs(project)
    for rel in plan.dirs:
        _mkdirs(project.joinpath(*rel.split('/')))
    try:
        used = _write(plan, project, mode, reserve, abort, link)
    except BaseException:
        remove_root(root, layout)
        raise
    return WorkingCopy(root=root, project_dir=project, uproject=project / f"{plan.stem}.uproject",
                       mode=used, files=len(plan.sources), seconds=time.monotonic() - started)


def remove_root(root: Path, layout: CacheLayout) -> bool:
    """Delete a working-copy root (renamed to ``trash/`` first). True when gone."""
    moved = fs_safe.move_aside(root, layout.trash)
    return fs_safe.remove_tree(moved or root)


def remove(working_copy: Optional[WorkingCopy], layout: CacheLayout) -> bool:
    return True if working_copy is None else remove_root(working_copy.root, layout)
