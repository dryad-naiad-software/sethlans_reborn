# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which Blender builds the manager keeps, and the row writes outside a fetch (BS-MGR-3a..5, BS-MGR-9).

Wanted = {``resolved_version`` of every supported series} x {every non-empty
``Worker.blender_platform``} (BS-MGR-4, D7). :func:`reconcile` changes rows
only, never files: it inserts a ``QUEUED`` row for each wanted build without
one and deletes the rows no longer wanted. The runner's sweep then removes
the files no row names (BS-MGR-8).

Writes follow BS-MGR-3a: inserts only through ``bulk_create(ignore_conflicts=True)``,
deletes only through ``filter(...).delete()``, and changes only through
conditional ``filter(pk=..., status=<expected>).update(..., updated_at=now)``.
``_rows_lock`` runs the reconciles of this process (request threads'
on-commit hooks, the runner) and the metadata view's single insert one at a
time, so a reconcile working from an older wanted set cannot re-insert a row
that a newer one just deleted.
"""

import logging
import threading
from typing import Set, Tuple

from django.db.models import Q
from django.utils import timezone

from shared.blender_sources import build_file_name, is_platform_id, is_version

from ...constants import BlenderBuildStatus
from ...models import BlenderBuild, SupportedBlenderVersion, Worker
from . import paths

logger = logging.getLogger(__name__)

Build = Tuple[str, str]  # (version, platform)

_rows_lock = threading.Lock()


def wanted_versions() -> Set[str]:
    values = SupportedBlenderVersion.objects.values_list('resolved_version', flat=True)
    return {v for v in values if is_version(v)}


def wanted_platforms() -> Set[str]:
    values = Worker.objects.exclude(blender_platform='').values_list('blender_platform', flat=True).distinct()
    return {p for p in values if is_platform_id(p)}


def wanted_set() -> Set[Build]:
    """Every (version, platform) the manager should keep (BS-MGR-4)."""
    platforms = wanted_platforms()
    return {(version, platform) for version in wanted_versions() for platform in platforms}


def is_wanted(version: str, platform: str) -> bool:
    """True when ``version`` is a supported resolved version and a worker reports ``platform``."""
    return (
        is_version(version) and is_platform_id(platform)
        and SupportedBlenderVersion.objects.filter(resolved_version=version).exists()
        and Worker.objects.filter(blender_platform=platform).exists()
    )


def _new_row(version: str, platform: str) -> BlenderBuild:
    return BlenderBuild(version=version, platform=platform, file_name=build_file_name(version, platform),
                        status=BlenderBuildStatus.QUEUED)


def reconcile() -> Tuple[int, int]:
    """Insert QUEUED rows for wanted builds without one, delete unwanted rows; returns (added, removed).

    Rows only, never files (BS-MGR-5). The caller wakes the runner.
    """
    with _rows_lock:
        wanted = wanted_set()
        existing = set(BlenderBuild.objects.values_list('version', 'platform'))
        missing = sorted(wanted - existing)
        BlenderBuild.objects.bulk_create([_new_row(v, p) for v, p in missing], ignore_conflicts=True)
        removed = 0
        for version, platform in sorted(existing - wanted):
            removed += BlenderBuild.objects.filter(version=version, platform=platform).delete()[0]
    if missing or removed:
        logger.info("Blender cache: %d builds queued, %d no longer wanted.", len(missing), removed)
    return len(missing), removed


def ensure_row(version: str, platform: str) -> bool:
    """Insert the QUEUED row of one wanted build without a full reconcile (BS-MGR-5); True when wanted."""
    with _rows_lock:
        if not is_wanted(version, platform):
            return False
        BlenderBuild.objects.bulk_create([_new_row(version, platform)], ignore_conflicts=True)
    return True


def requeue(row_id: int, expected: str) -> bool:
    """Conditional move of one row from ``expected`` back to QUEUED; True when it changed (BS-MGR-3a)."""
    return BlenderBuild.objects.filter(pk=row_id, status=expected).update(
        status=BlenderBuildStatus.QUEUED, updated_at=timezone.now()) == 1


def requeue_interrupted() -> int:
    """Start step: FETCHING rows a stopped process left go back to QUEUED (BS-MGR-6)."""
    return BlenderBuild.objects.filter(status=BlenderBuildStatus.FETCHING).update(
        status=BlenderBuildStatus.QUEUED, updated_at=timezone.now())


def requeue_missing_files() -> int:
    """Start step: each READY row whose file is missing or of another size goes back to QUEUED (BS-MGR-9)."""
    root = paths.cache_root()
    moved = 0
    for row in BlenderBuild.objects.filter(status=BlenderBuildStatus.READY):
        if paths.ready_file(row, root) is None and requeue(row.pk, BlenderBuildStatus.READY):
            logger.info("Blender cache: %s is missing on disk; queued again.", row.file_name)
            moved += 1
    return moved


def due_q(now) -> Q:
    """Rows the runner may claim: QUEUED, or FAILED whose retry time has come (BS-MGR-6)."""
    return Q(status=BlenderBuildStatus.QUEUED) | Q(
        Q(next_try_at__isnull=True) | Q(next_try_at__lte=now), status=BlenderBuildStatus.FAILED)
