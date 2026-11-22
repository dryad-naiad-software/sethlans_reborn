# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pilot jobs and measurement records (Unreal foundation FR-PM-1, FR-PM-2,
FR-PM-6, FR-PM-10, D2; per-machine memory test D5, FR-MT-R-5).

A normal Unreal job goes to a machine only after that machine's own
**pilot** job measured its key.  The ``UnrealStrategy`` hooks call
:func:`ensure_pilot` and friends, which create one targeted pilot per
eligible (worker, card kind) pair (``services/machine_tests.py``); the
admin retry is :func:`retry_measurement`.  Results are written only by
``POST /api/jobs/{id}/pilot-result/``.

**Lock first (FR-PM-10).**  Every write that can create a pilot opens
``transaction.atomic()`` and runs :func:`lock_project` as its first
statement: a zero-row ``UPDATE`` of the project (SQLite takes its write
lock), then ``SELECT ... FOR UPDATE`` of the project row only.  Two
requests on one project therefore serialize from their first statement.
:func:`ensure_pilot` asserts the lock is held in the current transaction
and computes the key from the locked, re-read row.  Lock order (D15):
Project, then Job rows, then the pair's Worker row, then its records.
"""

import logging
from dataclasses import dataclass

from django.db import transaction

from ..constants import UnrealKind
from ..models import Job, JobStatus, Project
from ..parent_status import take_write_lock
from ..renderers.unreal_measured import MEASURED, NONE, PENDING, key_aliases, key_reason, key_state_case
from .row_locks import lock_own_rows
from .unreal_target import Target

logger = logging.getLogger(__name__)

# Per-connection marker: ``{project_pk: (LockToken, locked project)}``.
_MARKER = '_sethlans_unreal_project_locks'
# ``take_write_lock`` field on Project: always '' or a 64-hex hash.
_LOCK_FIELD = 'unreal_overrides_sha256'


class ProjectLockMissing(RuntimeError):
    """``ensure_pilot`` ran without ``lock_project`` in this transaction."""


@dataclass(frozen=True)
class KeyState:
    """A key's farm-wide state and its failed record's reason (``renderers/unreal_measured.py``)."""

    state: str | None
    reason: str | None


_UNKNOWN = KeyState(None, None)
_KEYED_KINDS = (UnrealKind.RENDER, UnrealKind.AUDIO)


def key_state_of(project, asset_pk, overrides, target_hash):
    """The state of an explicit key, in one statement (carried by the project row)."""
    refs = (asset_pk, overrides, target_hash)
    row = Project.objects.filter(pk=project.pk).alias(**key_aliases(refs, '_ek_')).annotate(
        _state=key_state_case('_ek_'), _reason=key_reason(refs),
    ).values('_state', '_reason').first()
    if row is None:
        # The project is gone: no record of it can exist either.
        return KeyState(NONE, None)
    return KeyState(row['_state'], row['_reason'])


def key_state(job):
    """The key state of a saved render or audio ``job`` in one statement (no annotations needed).

    Blender jobs, pilots and ``shots`` jobs read ``KeyState(None, None)`` with no query.
    """
    if job.unreal_kind not in _KEYED_KINDS or not job.unreal_target_sha256:
        return _UNKNOWN
    from ..renderers.unreal_measured import note_annotations
    row = note_annotations(Job.objects.filter(pk=job.pk)).values('_unreal_key_state', '_unreal_key_reason').first()
    if row is None:
        return _UNKNOWN
    return KeyState(row['_unreal_key_state'], row['_unreal_key_reason'])


class LockToken:
    """``on_commit`` callback that marks one lock of one transaction (a project's, or the Worker locks).

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
    (``_unreal_key_state``, ``_unreal_key_reason``), so a single-object
    response reads it once.
    """
    if hasattr(job, '_unreal_key_state'):
        return KeyState(job._unreal_key_state, job._unreal_key_reason)
    found = key_state(job)
    job._unreal_key_state, job._unreal_key_reason = found.state, found.reason
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
    token = LockToken(connection, project.pk)
    transaction.on_commit(token)
    markers = getattr(connection, _MARKER, None)
    if markers is None:
        markers = {}
        setattr(connection, _MARKER, markers)
    markers[project.pk] = (token, locked)
    return locked


def locked_project(project):
    """Return the row :func:`lock_project` locked in this transaction.

    The lock counts only while its :class:`LockToken` is pending: taken in
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


def create_pilot(project, asset, target, *, worker, card_kind, reason, frame=0, remeasure=False):
    """A pair's targeted pilot and record (``services/machine_tests.create_pilot``, FR-MT-R-5)."""
    from .machine_tests import create_pilot as _create
    return _create(project, asset, target, worker=worker, card_kind=card_kind, reason=reason,
                   frame=frame, remeasure=remeasure)


def ensure_pilot(project, asset, target, *, reason, frame=0):
    """Create the pilots of a key for every eligible pair that needs one (FR-PM-2, FR-MT-R-5).

    Returns the new pilot jobs (empty when every eligible pair fits, does
    not fit, is testing or failed; after a failure only the admin retry
    creates a pilot).

    Raises:
        ProjectLockMissing: the caller did not take the project lock.
    """
    from .machine_tests import ensure_machine_tests
    return ensure_machine_tests(project, asset, target, reason=reason, frame=frame)


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
        created += ensure_pilot(project, job.asset, Target.of(job), reason=reason, frame=job.start_frame)
    return created


def ensure_pilot_for_job(job, *, reason):
    """Trigger (a) / (a'): the key of one QUEUED normal job; the new pilot jobs."""
    if job.unreal_kind != UnrealKind.RENDER or job.status != JobStatus.QUEUED:
        return []
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
