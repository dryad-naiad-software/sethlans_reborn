# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pipeline entry points the Unreal executor calls (Unreal foundation
FR-UE-2 phases 2-4 and 10, FR-PA-10 release order, FR-PA-16/17).

``prepare_workspace(cache, request, ...)``:

1. verifies ``CACHE_DIR`` (owner-only ACL and owner, no reparse point);
2. obtains the pristine entry for the pin: a verified hit with no share
   access, or (under the pin's populate lock) a reachability check, the
   copy from ``resolve(request.share_root, archive_relpath)`` and the pin
   check, then extraction and publish (``populate``);
3. stages the overlay (if any) into ``o/<job_id>-<att8>/`` after its own
   reachability check, and checks its pin before any materialization;
4. materializes ``w/<key16><ov8>-s<slot>/<ProjectStem>/``.

Any failure undoes what this call made (staged overlay, refcount). Share
access resolves against ``request.share_root`` only (Conc R2-M2c).

``JobWorkspace.finish(survivors=...)`` runs, in this order: the FR-PA-16
integrity check (with survivors it is skipped and the entry quarantined),
deletion of the working copy and of the staged overlay, then
``cache.release`` (FR-PA-10). The executor calls it only after process
exit is confirmed (FR-UE-11), with an ``Abort`` that has **no deadline**;
any exception inside the check counts as ``own_ok=False`` (no upload).

FR-UE-24's Zen/DDC guard (``unreal_executor.zen_guard``) runs on the
returned ``working_copy.project_dir`` before any UE launch (step 4a).
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from sethlans_worker_agent.project_archive import eviction, fs_safe, integrity, overlay, populate, share_copy
from sethlans_worker_agent.project_archive import working_copy as wc
from sethlans_worker_agent.project_archive.abort import NEVER, Abort
from sethlans_worker_agent.project_archive.cache import ArchiveCache
from sethlans_worker_agent.project_archive.errors import ShareUnavailableError
from sethlans_worker_agent.project_archive.layout import safe_job_token
from sethlans_worker_agent.project_archive.limits import PipelineLimits
from sethlans_worker_agent.project_archive.reachability import resolve_share_path
from shared.unreal.unc import UncPath

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveRequest:
    """What one claim attempt needs (from the validated ``JobParams``)."""
    job_id: int
    attempt: str  # att8
    share_root: UncPath
    archive_relpath: str
    archive_sha256: str
    uproject_relpath: str
    overlay_relpath: str = ''
    overlay_sha256: str = ''
    slot: int = 0


def default_resolve(root: UncPath, relpath: str, kind: str) -> str:
    return resolve_share_path(root, relpath, kind).canonical


@dataclass
class JobWorkspace:
    cache: ArchiveCache
    request: ArchiveRequest
    entry: populate.AcquiredEntry
    overlay: Optional[overlay.StagedOverlay]
    working_copy: wc.WorkingCopy
    summary: Dict[str, str] = field(default_factory=dict)
    _finished: bool = False

    def finish(self, *, survivors: bool = False, abort: Abort = NEVER) -> integrity.IntegrityReport:
        """Integrity check, then cleanup, then release (FR-PA-10 order). Idempotent."""
        if self._finished:
            return integrity.skipped('already finished')
        self._finished = True
        sha = self.entry.sha256
        try:
            if survivors:
                # UE may still be writing through the hard links: never reuse this entry (Conc C6).
                self.cache.quarantine(sha, 'integrity skipped (survivors)')
                report = integrity.skipped('survivors')
            else:
                try:
                    report = integrity.check_after_job(self.cache, sha, self.entry.record, abort)
                except Exception as exc:  # noqa: BLE001 - an unverifiable entry is a failed one
                    logger.error("Pristine integrity check of %s failed: %s", sha[:16], exc)
                    self.cache.quarantine(sha, f"integrity check failed: {exc}")
                    report = integrity.IntegrityReport(own_ok=False, failures={sha[:16]: 0})
            self.summary['pristine_integrity'] = report.summary.split('=', 1)[1]
            return report
        finally:
            try:
                wc.remove(self.working_copy, self.cache.layout)
                if self.overlay is not None:
                    fs_safe.remove_tree(self.overlay.path)
            finally:
                self.cache.release(self.entry.sha256)


def _require_reachable(reach, request: ArchiveRequest, relpath: str, kind: str) -> None:
    if not reach.check(request.share_root, relpath, kind, job_id=request.job_id, pool='job'):
        what = 'archive' if kind == 'file' else 'overlay'
        raise ShareUnavailableError(f"The {what} is not reachable on the Sethlans share.")


def _fetcher(cache, request, limits, reach, resolve, share_validator):
    def fetch(abort: Abort):
        _require_reachable(reach, request, request.archive_relpath, 'file')
        unc = resolve(request.share_root, request.archive_relpath, 'file')
        return share_copy.copy_archive(
            unc, cache.layout.check_child('downloads'), request.archive_sha256, abort,
            max_bytes=limits.max_archive_bytes, stall_seconds=limits.copy_stall_seconds,
            reserve=lambda n: eviction.reserve(cache, n), share_validator=share_validator)
    return fetch


def _stage(cache, request, reach, resolve, abort, share_validator, digest_fn, stall_seconds):
    _require_reachable(reach, request, request.overlay_relpath, 'dir')
    cache.layout.check_child('o')
    dest = cache.layout.overlay_dir(request.job_id, request.attempt)
    try:
        return overlay.stage_overlay(
            resolve(request.share_root, request.overlay_relpath, 'dir'), dest, request.overlay_sha256, abort,
            reserve=lambda n: eviction.reserve(cache, n), share_validator=share_validator, digest_fn=digest_fn,
            stall_seconds=stall_seconds)
    except BaseException:
        fs_safe.remove_tree(dest)  # an abandoned worker removes whatever it writes later
        raise


def prepare_workspace(cache: ArchiveCache, request: ArchiveRequest, *, limits: PipelineLimits, reachability,
                      abort: Abort = NEVER, resolve: Callable = default_resolve,
                      share_validator: Callable = share_copy.default_share_validator,
                      overlay_validator: Callable = overlay.default_share_validator,
                      digest_fn: Callable = overlay.default_digest, **materialize_kw) -> JobWorkspace:
    """Acquire, populate and materialize the working copy for one job; see the module docstring."""
    safe_job_token(request.attempt)
    cache.layout.verify_root()
    fetch = _fetcher(cache, request, limits, reachability, resolve, share_validator)
    entry = populate.obtain_entry(cache, request.archive_sha256, fetch, abort, limits.max_uncompressed_bytes)
    staged = None
    try:
        if request.overlay_relpath:
            staged = _stage(cache, request, reachability, resolve, abort, overlay_validator, digest_fn,
                            limits.copy_stall_seconds)
        cache.layout.check_child('w')
        copy = wc.materialize(
            cache.layout, entry.tree, request.uproject_relpath, staged, sha256=entry.sha256,
            overlay_sha256=request.overlay_sha256 if staged else '', slot=request.slot,
            mode=limits.workcopy_mode, reserve=lambda n: eviction.reserve(cache, n), abort=abort, **materialize_kw)
    except BaseException:
        if staged is not None:
            fs_safe.remove_tree(staged.path)
        cache.release(entry.sha256)
        raise
    summary = {
        'archive_sha256': entry.sha256, 'cache': 'hit' if entry.hit else 'miss',
        'copy_s': f"{entry.copy_s:.1f}", 'extract_s': f"{entry.extract_s:.1f}",
        'overlay_sha256': staged.digest if staged else '', 'overlay_files': str(len(staged.files) if staged else 0),
        'workcopy': copy.mode, 'workcopy_files': str(copy.files), 'workcopy_s': f"{copy.seconds:.2f}",
        'workcopy_root': str(copy.root),
    }
    logger.info("Unreal working copy ready for job %d: %s", request.job_id,
                ' '.join(f"{k}={v}" for k, v in summary.items()))
    return JobWorkspace(cache=cache, request=request, entry=entry, overlay=staged, working_copy=copy,
                        summary=summary)
