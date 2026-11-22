# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-machine memory tests: which (worker, card kind) pairs test a key, and
creating their targeted pilots (Unreal per-machine memory test D2, D4, D5,
D15, FR-MT-R-5, FR-MT-P-1).

**Eligible pair (D5):** an active worker seen within
``WORKER_STALENESS_SECONDS`` that reports the key's engine build and the
manager's share as reachable and has a live card reading, with one of its
``worker_kinds`` (at most four).  A worker with the VRAM override has no
live card reading and is never eligible (D14).

:func:`ensure_machine_tests` creates a targeted pilot for every eligible
pair of a key that has no current record, or whose current record is
``retest_due``.  A pair that is testing, fits, does not fit or failed gets
none (a failed pair waits for the admin retry).  A worker with
``MAX_OPEN_PILOTS`` QUEUED targeted pilots gets no more.

**Locks (D15):** the caller holds ``lock_project``; then every Worker row
is locked once per transaction in ascending pk order
(``worker_locks.lock_workers``, before any record lock, so several keys in
one transaction never lock workers out of order); :func:`create_pilot`
then locks the pair's current records (by pk), moves ``current`` to the
new record and inserts the pilot job.  The open-pilot cap is counted after
the Worker locks, so concurrent projects cannot pass it.
"""

import logging
from datetime import timedelta

from django.db.models import Count, Min
from django.utils import timezone

from ..constants import PILOT_NAME_PREFIX, WORKER_STALENESS_SECONDS, RenderDevice, UnrealKind
from ..constants_sequence import CARD_KIND_KEY
from ..models import Job, JobStatus, UnrealMeasurement, Worker
from ..models.workers import reports_share_reachable
from ..renderers.unreal_machine import RETEST_DUE, eligible_kinds, live_pilot, pair_state_case
from ..renderers.unreal_settings import MAP, OUTPUT_FORMAT, PRESET, SEQUENCE
from .row_locks import lock_own_rows
from .sethlans_share import configured_share
from .unreal_pilot import locked_project
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

# D5: open (QUEUED) targeted pilots one worker may have.
MAX_OPEN_PILOTS = 20


def eligible_pairs(build, workers=None, within=None):
    """The D5 ``(worker, kind)`` pairs for ``build``, by worker pk then kind.

    ``workers`` narrows them; ``within`` (the pks :func:`lock_workers` locked) too.
    """
    share = configured_share()
    if not share:
        return []
    cutoff = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
    queryset = Worker.objects.filter(is_active=True, last_seen__gte=cutoff).order_by('pk')
    if within is not None:
        queryset = queryset.filter(pk__in=sorted(within))
    if workers is not None:
        queryset = queryset.filter(pk__in=[getattr(worker, 'pk', worker) for worker in workers])
    pairs = []
    for worker in queryset:
        if worker.has_unreal_build(build) and reports_share_reachable(worker.unreal_capabilities, share):
            pairs.extend((worker, kind) for kind in eligible_kinds(worker))
    return pairs


def pair_states(asset, overrides, target_hash, pairs):
    """``{(worker pk, kind): D2 state}`` of the pairs' current records (one query; missing: not tried)."""
    if not pairs:
        return {}
    records = UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=overrides, target_sha256=target_hash, current=True,
        worker__in=sorted({worker.pk for worker, _kind in pairs}),
    ).annotate(_state=pair_state_case())
    return {(worker_id, kind): state for worker_id, kind, state in records.values_list('worker_id', 'card_kind', '_state')}


def open_pilot_counts(worker_pks):
    """``{worker pk: QUEUED live targeted pilots}`` (one query).

    Only pilots that can still run count (current record, current CVars): a
    stale or superseded QUEUED pilot never shuts a worker out of new tests.
    """
    rows = Job.objects.filter(
        target_worker__in=sorted(worker_pks), status=JobStatus.QUEUED, unreal_kind=UnrealKind.PILOT,
    ).filter(live_pilot()).values('target_worker').annotate(_n=Count('pk')).values_list('target_worker', '_n')
    return dict(rows)


def _queue_position(asset, target_hash, now):
    """``min(now, oldest QUEUED normal job of the key)`` (FR-PM-2 queue position)."""
    oldest = Job.objects.filter(
        asset=asset, unreal_kind=UnrealKind.RENDER, status=JobStatus.QUEUED,
        unreal_target_sha256=target_hash,
    ).aggregate(oldest=Min('submitted_at'))['oldest']
    return min(now, oldest) if oldest is not None else now


def _make_current(project, asset, target_hash, worker, card_kind):
    """With every Worker row locked, lock the pair's current records by pk and clear their ``current`` (D15)."""
    if worker.pk not in lock_workers():
        return False
    pair = UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=project.unreal_overrides_sha256, target_sha256=target_hash,
        worker=worker, card_kind=card_kind, current=True,
    )
    old = list(lock_own_rows(pair).order_by('pk').values_list('pk', flat=True))
    if old:
        UnrealMeasurement.objects.filter(pk__in=old).update(current=False)
    return True


def create_pilot(project, asset, target, *, worker, card_kind, reason, frame=0, remeasure=False):
    """Insert the pair's new current record and its targeted pilot job (FR-MT-R-5).

    Runs in the caller's locked transaction (``lock_project``).  The
    record first (its pk names the job), then the job, then the record's
    ``pilot_job``.  A sequence target's resolution, shot set, draft and
    frames go into the pilot's ``render_settings`` (D8, FR-SR-1), so its
    key equals its pieces' key; ``unreal.card_kind`` names the card kind
    it measures.  Returns ``None`` when the worker row was gone when the
    Worker rows were locked.
    """
    now = timezone.now()
    target_hash = target.sha256()
    if not _make_current(project, asset, target_hash, worker, card_kind):
        return None
    record = UnrealMeasurement.objects.create(
        project=project, asset=asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=target_hash, created_at=now, engine_build=target.engine_build,
        map=target.map, sequence=target.sequence, preset=target.preset, remeasure=remeasure,
        worker=worker, worker_hostname=worker.hostname, card_kind=card_kind, current=True,
        base_target_sha256=target.base_sha256(), measured_ranges=target.rendered_ranges(),
    )
    name = f"{PILOT_NAME_PREFIX}{record.pk}"
    frame = target.pilot_frame(frame)
    pilot = Job.objects.create(
        name=name, asset=asset, output_file_pattern=f"//sethlans_pilot/{name}.png",
        engine_build=target.engine_build, unreal_kind=UnrealKind.PILOT, target_worker=worker,
        render_device=RenderDevice.GPU, start_frame=frame, end_frame=frame,
        render_settings={MAP: target.map, SEQUENCE: target.sequence, PRESET: target.preset,
                         OUTPUT_FORMAT: 'PNG', **target.pilot_settings(), CARD_KIND_KEY: card_kind},
        status=JobStatus.QUEUED, submitted_at=_queue_position(asset, target_hash, now),
    )
    UnrealMeasurement.objects.filter(pk=record.pk).update(pilot_job=pilot)
    record.pilot_job = pilot
    logger.info(
        "Created pilot job %s for Unreal project %s on worker %s, card kind %s (asset %s, target %s %s %s %s, "
        "reason %s).", pilot.pk, project.pk, worker.hostname, card_kind, asset.pk, target.map, target.sequence,
        target.preset, target.engine_build, reason,
    )
    return record


def create_pilots(project, asset, target, pairs, *, reason, frame=0, remeasured=frozenset()):
    """One :func:`create_pilot` per pair, within the D5 cap of open pilots per worker; the new records.

    ``remeasured`` holds the ``(worker pk, kind)`` pairs measured again by an admin (``remeasure``).
    """
    lock_workers()  # D15, D5: count the cap under the Worker locks
    counts = open_pilot_counts({worker.pk for worker, _kind in pairs})
    records = []
    for worker, kind in pairs:
        if counts.get(worker.pk, 0) >= MAX_OPEN_PILOTS:
            logger.warning(
                "Worker %s already has %d queued memory tests; no pilot for project %s, card kind %s.",
                worker.hostname, MAX_OPEN_PILOTS, project.pk, kind,
            )
            continue
        record = create_pilot(project, asset, target, worker=worker, card_kind=kind, reason=reason,
                              frame=frame, remeasure=(worker.pk, kind) in remeasured)
        if record is not None:
            counts[worker.pk] = counts.get(worker.pk, 0) + 1
            records.append(record)
    return records


def ensure_machine_tests(project, asset, target, *, reason, frame=0, workers=None):
    """Create a targeted pilot for every eligible pair of the key that needs one (D5, FR-MT-P-1).

    Returns the new pilot jobs (possibly none).

    Raises:
        ProjectLockMissing: the caller did not take the project lock.
    """
    locked = locked_project(project)
    pairs = eligible_pairs(target.engine_build, workers, within=lock_workers())
    states = pair_states(asset, locked.unreal_overrides_sha256, target.sha256(), pairs)
    # D2: no current record (not tried), or a current record that is retest_due.
    needing = [(worker, kind) for worker, kind in pairs
               if states.get((worker.pk, kind)) in (None, RETEST_DUE)]
    return [record.pilot_job for record in create_pilots(locked, asset, target, needing, reason=reason, frame=frame)]
