# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pilot jobs and measurement records (Unreal foundation FR-PM-1, FR-PM-2,
FR-PM-6, FR-PM-10, D2).

A normal Unreal job is held until its measurement key is measured by a
manager-created **pilot** job.  Records are created only here
(:func:`ensure_pilot` from the ``UnrealStrategy`` hooks, and
:func:`retry_measurement` from the admin retry); their results are
written only by ``POST /api/jobs/{id}/pilot-result/``.

**Lock first (FR-PM-10).**  Every write that can create a pilot opens
``transaction.atomic()`` and runs :func:`lock_project` as its first
statement: a zero-row ``UPDATE`` of the project (SQLite takes its write
lock), then ``SELECT ... FOR UPDATE`` of the project row only.  Two
requests on one project therefore serialize from their first statement.
:func:`ensure_pilot` asserts the lock is held in the current transaction
and computes the key from the locked, re-read row.  Lock order: Project,
then Asset / Job / UnrealMeasurement.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from shared.unreal.validation import target_sha256

from ..constants import PILOT_NAME_PREFIX, RenderDevice, UnrealKind
from ..models import Job, JobStatus, Project, UnrealMeasurement
from ..parent_status import take_write_lock
from ..renderers.unreal_measured import (
    MEASURED, NONE, PENDING, key_aliases, key_peak, key_reason, key_state_case,
)
from ..renderers.unreal_settings import MAP, OUTPUT_FORMAT, PRESET, SEQUENCE
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

# Per-connection marker: ``{project_pk: (_LockToken, locked project)}``.
_MARKER = '_sethlans_unreal_project_locks'
# ``take_write_lock`` field on Project: always '' or a 64-hex hash.
_LOCK_FIELD = 'unreal_overrides_sha256'


class ProjectLockMissing(RuntimeError):
    """``ensure_pilot`` ran without ``lock_project`` in this transaction."""


@dataclass(frozen=True)
class Target:
    """What a pilot renders: the job's build and its map, sequence and preset."""

    engine_build: str
    map: str
    sequence: str
    preset: str

    @classmethod
    def of(cls, job):
        settings = job.render_settings if isinstance(job.render_settings, dict) else {}
        return cls(job.engine_build, settings.get(MAP, ''), settings.get(SEQUENCE, ''),
                   settings.get(PRESET, ''))

    def sha256(self):
        return target_sha256(self.engine_build, self.map, self.sequence, self.preset)


@dataclass(frozen=True)
class KeyState:
    """A key's state, the latest record's failure reason and the measured peak."""

    state: str | None
    reason: str | None
    peak_mb: int | None


def key_state_of(project, asset_pk, overrides, target_hash):
    """The state of an explicit key, in one statement (carried by the project row)."""
    refs = (asset_pk, overrides, target_hash)
    row = Project.objects.filter(pk=project.pk).alias(
        **key_aliases(refs, '_ek_'),
    ).annotate(
        _state=key_state_case('_ek_'), _reason=key_reason(refs), _peak=key_peak(refs),
    ).values('_state', '_reason', '_peak').first()
    if row is None:
        # The project is gone: no record of it can exist either.
        return KeyState(NONE, None, None)
    return KeyState(row['_state'], row['_reason'], row['_peak'])


def key_state(job):
    """The key state of a saved ``job`` in one statement (no annotations needed).

    Blender jobs and pilots read ``KeyState(None, None, None)`` with no query.
    """
    if job.unreal_kind != UnrealKind.RENDER or not job.unreal_target_sha256:
        return KeyState(None, None, None)
    from ..renderers.unreal_measured import job_annotations
    row = job_annotations(Job.objects.filter(pk=job.pk)).values(
        '_unreal_key_state', '_unreal_key_reason', '_unreal_peak_mb',
    ).first()
    if row is None:
        return KeyState(None, None, None)
    return KeyState(row['_unreal_key_state'], row['_unreal_key_reason'], row['_unreal_peak_mb'])


class _LockToken:
    """``on_commit`` callback that marks one project lock of one transaction.

    Registered when the lock is taken: a rollback (of the transaction or
    of the savepoint the lock was taken in) discards it, and a commit runs
    it, which clears the marker.  So a marker is valid only while its token
    is still pending in ``connection.run_on_commit``.
    """

    def __init__(self, connection, project_pk):
        self.connection = connection
        self.project_pk = project_pk
        # The innermost savepoint at lock time (None: the outermost block).
        self.savepoint = connection.savepoint_ids[-1] if connection.savepoint_ids else None

    def __call__(self):
        markers = getattr(self.connection, _MARKER, {})
        entry = markers.get(self.project_pk)
        if entry is not None and entry[0] is self:
            del markers[self.project_pk]

    def is_live(self):
        connection = self.connection
        if not connection.in_atomic_block:
            return False
        if self.savepoint is not None and self.savepoint not in connection.savepoint_ids:
            return False
        return any(func is self for _sids, func, _robust in connection.run_on_commit)


def cached_key_state(job):
    """The job's key state from its annotations, else :func:`key_state` once.

    The result is stored on the instance as the list annotations are
    (``_unreal_key_state``, ``_unreal_key_reason``, ``_unreal_peak_mb``),
    so a single-object response reads it once and its fields agree.
    """
    if hasattr(job, '_unreal_key_state'):
        return KeyState(job._unreal_key_state, job._unreal_key_reason, job._unreal_peak_mb)
    found = key_state(job)
    job._unreal_key_state, job._unreal_key_reason, job._unreal_peak_mb = found.state, found.reason, found.peak_mb
    return found


def lock_project(project):
    """Write-lock and re-read ``project`` as the transaction's first statement.

    Returns:
        The locked ``Project`` row, re-read from the database.

    Raises:
        RuntimeError: called outside ``transaction.atomic()``.
    """
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise RuntimeError("lock_project must run inside transaction.atomic()")
    take_write_lock(Project.objects.filter(pk=project.pk), field=_LOCK_FIELD)
    locked = lock_own_rows(Project.objects).get(pk=project.pk)
    token = _LockToken(connection, project.pk)
    transaction.on_commit(token)
    markers = getattr(connection, _MARKER, None)
    if markers is None:
        markers = {}
        setattr(connection, _MARKER, markers)
    markers[project.pk] = (token, locked)
    return locked


def locked_project(project):
    """Return the row :func:`lock_project` locked in this transaction.

    The lock counts only while its :class:`_LockToken` is pending: taken in
    the current transaction, in a savepoint that is still open, and neither
    committed nor rolled back.

    Raises:
        ProjectLockMissing: no lock on ``project`` in the current transaction.
    """
    connection = transaction.get_connection()
    entry = (getattr(connection, _MARKER, None) or {}).get(project.pk)
    if entry is None or not entry[0].is_live():
        raise ProjectLockMissing(
            f"ensure_pilot needs the project lock (lock_for_write) for project {project.pk}."
        )
    return entry[1]


def _queue_position(asset, target_hash, now):
    """``min(now, oldest QUEUED normal job of the key)`` (FR-PM-2 queue position)."""
    oldest = Job.objects.filter(
        asset=asset, unreal_kind=UnrealKind.RENDER, status=JobStatus.QUEUED,
        unreal_target_sha256=target_hash,
    ).aggregate(oldest=Min('submitted_at'))['oldest']
    return min(now, oldest) if oldest is not None else now


def create_pilot(project, asset, target, *, reason, frame=0, remeasure=False):
    """Insert a measurement record and its pilot job (FR-PM-2 step 4).

    Runs in the caller's locked transaction: the record first (its pk
    names the job), then the job, then the record's ``pilot_job``.
    """
    now = timezone.now()
    target_hash = target.sha256()
    record = UnrealMeasurement.objects.create(
        project=project, asset=asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=target_hash, created_at=now, engine_build=target.engine_build,
        map=target.map, sequence=target.sequence, preset=target.preset, remeasure=remeasure,
    )
    name = f"{PILOT_NAME_PREFIX}{record.pk}"
    pilot = Job.objects.create(
        name=name, asset=asset, output_file_pattern=f"//sethlans_pilot/{name}.png",
        engine_build=target.engine_build, unreal_kind=UnrealKind.PILOT,
        render_device=RenderDevice.GPU, start_frame=frame, end_frame=frame,
        render_settings={MAP: target.map, SEQUENCE: target.sequence, PRESET: target.preset,
                         OUTPUT_FORMAT: 'PNG'},
        status=JobStatus.QUEUED, submitted_at=_queue_position(asset, target_hash, now),
    )
    UnrealMeasurement.objects.filter(pk=record.pk).update(pilot_job=pilot)
    record.pilot_job = pilot
    logger.info(
        "Created pilot job %s for Unreal project %s (asset %s, target %s %s %s %s, reason %s).",
        pilot.pk, project.pk, asset.pk, target.map, target.sequence, target.preset,
        target.engine_build, reason,
    )
    return record


def ensure_pilot(project, asset, target, *, reason, frame=0):
    """Create the pilot of a key that has no record yet (FR-PM-2).

    Returns ``None`` when the key is measured, pending or failed (after a
    failure only the admin retry creates a pilot), else the new pilot job.

    Raises:
        ProjectLockMissing: the caller did not take the project lock.
    """
    locked = locked_project(project)
    found = key_state_of(locked, asset.pk, locked.unreal_overrides_sha256, target.sha256())
    if found.state != NONE:
        return None
    return create_pilot(locked, asset, target, reason=reason, frame=frame).pilot_job


def ensure_pilots_for_queued(project, *, reason, asset=None):
    """One :func:`ensure_pilot` per distinct key of the QUEUED normal jobs (triggers b, c)."""
    jobs = Job.objects.filter(
        asset__project=project, unreal_kind=UnrealKind.RENDER, status=JobStatus.QUEUED,
    ).exclude(unreal_target_sha256='').select_related('asset').order_by('submitted_at', 'id')
    if asset is not None:
        jobs = jobs.filter(asset=asset)
    seen = set()
    created = []
    for job in jobs:
        key = (job.asset_id, job.unreal_target_sha256)
        if key in seen:
            continue
        seen.add(key)
        pilot = ensure_pilot(project, job.asset, Target.of(job), reason=reason, frame=job.start_frame)
        if pilot is not None:
            created.append(pilot)
    return created


def ensure_pilot_for_job(job, *, reason):
    """Trigger (a) / (a'): the key of one QUEUED normal job."""
    if job.unreal_kind != UnrealKind.RENDER or job.status != JobStatus.QUEUED:
        return None
    return ensure_pilot(job.asset.project, job.asset, Target.of(job), reason=reason,
                        frame=job.start_frame)


def retry_measurement(project, *, job_id=None, remeasure=False, username=''):
    """The admin retry (FR-PM-6); see ``services/unreal_retry.py``."""
    from .unreal_retry import retry_measurement as _retry
    return _retry(project, job_id=job_id, remeasure=remeasure, username=username)


__all__ = [
    'KeyState', 'MEASURED', 'NONE', 'PENDING', 'ProjectLockMissing', 'Target',
    'cached_key_state', 'create_pilot', 'ensure_pilot', 'ensure_pilot_for_job', 'ensure_pilots_for_queued',
    'key_state', 'key_state_of', 'lock_project', 'locked_project', 'retry_measurement',
]
