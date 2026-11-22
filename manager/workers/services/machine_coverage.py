# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Coverage: one machine's result serving a smaller or draft key (Unreal
per-machine memory test D8, Q5, OQ-1, FR-MT-P-2).

A current, not stale, ``fits`` record **R** of the same worker and card
kind covers key **K** when R's ``base_target_sha256`` (the target without
shot set and draft) equals K's, R was measured without a draft, and K's
needed ranges are a subset of R's ``measured_ranges`` (which keep only the
ranges whose start frame R's pilot rendered, so a sampled pilot covers
only the shots it measured).  A ``does_not_fit`` record covers nothing.
Standalone jobs (no shot set, no draft) match by exact key only, so they
are never covered; a key without needed ranges (a submission planned
before migration 0032) neither.

Only a record measured by its own pilot covers (``pilot_job`` set and
without ``unreal.draft`` in its settings): a derived record never covers
another key, so a spill on a derived record reaches its covering record in
one step (FR-MT-S-3).  Of several covering records the newest is used.

:func:`derive` creates K's derived record from R under the D15 locks:
every Worker row (``lock_workers``), then R and K's current records of
the pair together, by pk.  It re-checks R under the lock; when R is no
longer current, ``fits`` or not stale it creates nothing and the caller
creates a pilot instead.  In ``sequence_create.plan_submission`` this
record lock comes after the discovery and submission locks, as the pilot
records' do there (see its docstring for why that cannot deadlock).
"""

import logging

from django.db.models import Q
from django.utils import timezone

from ..constants_sequence import DRAFT_KEY
from ..models import UnrealMeasurement
from ..renderers.unreal_machine import FITS
from .row_locks import lock_own_rows
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

# Figures, driver and ranges a derived record copies from its covering record (D8).
COPIED_FIELDS = (
    'peak_mb', 'peak_source', 'game_overrides', 'texture_streaming', 'card_delta_peak_mb',
    'baseline_used_mb', 'baseline_drift_mb', 'peak_phase', 'max_sample_gap_s', 'prep_s', 'warmup_s',
    'warmup_ran', 'render_s', 'frames', 'seconds_per_frame', 'per_shot', 'worker_hostname', 'gpu_index',
    'gpu_name', 'gpu_total_mb', 'csv_local_used_peak_mb', 'ram', 'ram_process_commit_peak_mb',
    'shared_machine', 'measured_at', 'driver_version', 'verdict', 'shared_peak_mb', 'job_peak_max_mb',
    'lower_bound_mb', 'measured_ranges',
)


def _range_set(ranges):
    """``[start, end)`` pairs as a set of tuples; anything malformed counts as no range."""
    if not isinstance(ranges, (list, tuple, set, frozenset)):
        return set()
    return {tuple(pair) for pair in ranges if isinstance(pair, (list, tuple)) and len(pair) == 2}


def needed_ranges(target):
    """The ranges key ``target`` must have measured, or ``set()`` when it can only match exactly."""
    if not (target.shot_set_sha256 or target.draft):
        return set()
    return _range_set(target.measured_ranges)


def covering_records(asset, overrides, target, needed, pairs):
    """``{(worker pk, kind): R}`` for the pairs a record covers (D8; one query, subset in Python)."""
    needed = _range_set(needed)
    wanted = {(getattr(worker, 'pk', worker), kind) for worker, kind in pairs}
    if not needed or not wanted:
        return {}
    candidates = UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=overrides, base_target_sha256=target.base_sha256(), current=True,
        verdict=FITS, stale_reason='', pilot_job__isnull=False, measured_ranges__isnull=False,
        worker__in=sorted({worker for worker, _kind in wanted}), card_kind__in=sorted({kind for _w, kind in wanted}),
    ).exclude(target_sha256=target.sha256()).exclude(
        pilot_job__render_settings__has_key=DRAFT_KEY,
    ).order_by('-pk')
    found = {}
    for record in candidates:
        pair = (record.worker_id, record.card_kind)
        if pair in wanted and pair not in found and needed <= _range_set(record.measured_ranges):
            found[pair] = record
    return found


def covering_record(asset, overrides, target, needed, worker, kind):
    """The record of ``worker`` and ``kind`` that covers key ``target``, or ``None`` (FR-MT-P-2)."""
    pair = (getattr(worker, 'pk', worker), kind)
    return covering_records(asset, overrides, target, needed, [pair]).get(pair)


def derive(record, key):
    """Create key ``key``'s derived record from covering record ``record``; ``None`` when R changed.

    ``key`` is ``(locked project, asset, target)``.  Runs under the
    caller's ``lock_project``.  The derived record has no pilot job,
    ``verdict_source='covered'``, ``covered_by=R`` and R's figures, driver
    and measured ranges; it becomes the pair's current record.
    """
    project, asset, target = key
    if record.worker_id not in lock_workers():
        return None
    target_hash = target.sha256()
    pair = Q(asset=asset, overrides_sha256=project.unreal_overrides_sha256, target_sha256=target_hash,
             worker_id=record.worker_id, card_kind=record.card_kind, current=True)
    locked = {row.pk: row for row in lock_own_rows(
        UnrealMeasurement.objects.filter(pair | Q(pk=record.pk))).order_by('pk')}
    fresh = locked.pop(record.pk, None)
    if fresh is None or not fresh.current or fresh.verdict != FITS or fresh.stale_reason:
        return None
    if locked:
        UnrealMeasurement.objects.filter(pk__in=sorted(locked)).update(current=False)
    derived = UnrealMeasurement.objects.create(
        project=project, asset=asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=target_hash, created_at=timezone.now(), engine_build=target.engine_build,
        map=target.map, sequence=target.sequence, preset=target.preset, worker_id=fresh.worker_id,
        card_kind=fresh.card_kind, current=True, verdict_source='covered', covered_by=fresh,
        base_target_sha256=target.base_sha256(), **{field: getattr(fresh, field) for field in COPIED_FIELDS},
    )
    logger.info(
        "Unreal project %s: result %s of worker %s, card kind %s covers target %s %s %s %s (record %s).",
        project.pk, fresh.pk, fresh.worker_hostname, fresh.card_kind, target.map, target.sequence,
        target.preset, target.engine_build, derived.pk,
    )
    return derived
