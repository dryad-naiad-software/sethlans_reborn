# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job writes from the API that cannot undo a concurrent change (#217).

A worker status PATCH, an output upload and an admin PATCH used to save
the whole job row from an unlocked copy read at the start of the request,
so a cancel, requeue or worker report committed in between was silently
overwritten.

Every such write now runs inside ``transaction.atomic()`` and:

1. locks the job row only (:func:`lock_own_rows`, safe on PostgreSQL);
2. re-checks the locked row: a worker write needs the job still RENDERING
   and still assigned to that worker; a requested status change must be
   valid from the job's *current* status (``VALID_STATUS_TRANSITIONS``);
3. saves with ``update_fields`` limited to the fields the request sent.

A failed re-check raises :class:`JobWriteConflict` (the views map it to
HTTP 409) and nothing is written.  ``post_save`` still fires for the
saved row, inside the transaction and after the job lock, which keeps the
Job -> parent lock order documented in ``parent_status.py``.

The whole transaction is retried on SQLite lock errors (issue #118, see
:func:`run_with_lock_retry`), but never once it has committed: ``on_commit``
callbacks (thumbnail generation, assembly) run inside ``Atomic.__exit__``,
so their errors would otherwise look like a failed write.  See
:func:`_locked_write`.  Files are written to storage once, before the
transaction.
"""

import logging
import time

from django.db import OperationalError, transaction

from ..models import Job, JobStatus
from ..serializers.jobs import VALID_STATUS_TRANSITIONS
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

# Fields a worker may change through ``PATCH /api/jobs/{id}/``.  These are
# exactly what the worker agent sends (``job_lifecycle`` result report,
# crash report, and the GPU-drift cancel in ``job_processor``).
WORKER_PATCH_FIELDS = frozenset({
    'status',
    'completed_at',
    'last_output',
    'error_message',
    'render_time_seconds',
})

# Issue #118: brief retry on SQLite lock errors that ``busy_timeout``
# cannot wait on (``SQLITE_LOCKED`` in shared-cache mode, and the
# ``SQLITE_BUSY_SNAPSHOT`` a deferred transaction hits when it upgrades a
# stale read to a write).  Each retry doubles the back-off starting at
# 50 ms; with 6 attempts the worst case is ~3.1 s, well inside the 30 s
# HTTP window.
_SAVE_RETRY_ATTEMPTS = 6
_SAVE_RETRY_BASE_DELAY_S = 0.05


class JobWriteConflict(Exception):
    """The locked job no longer accepts this write (HTTP 409)."""


def run_with_lock_retry(operation, job_pk):
    """Run ``operation()`` and retry it on SQLite lock errors.

    ``operation`` must be safe to repeat: it runs a whole
    ``transaction.atomic()`` block, and a lock error rolls that block back
    before it is retried.  PostgreSQL and MySQL never raise these errors,
    so for them this is a single call.

    Returns:
        Whatever ``operation`` returns.

    Raises:
        OperationalError: a non-lock error at once, or the last lock error
            once the retries are used up (DRF maps it to a 500).
    """
    delay = _SAVE_RETRY_BASE_DELAY_S
    last_exc = None
    for attempt in range(_SAVE_RETRY_ATTEMPTS):
        try:
            return operation()
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            logger.warning(
                "Job write for job %s hit a SQLite lock on attempt "
                "%d/%d: %s. Retrying in %.3fs.",
                job_pk, attempt + 1, _SAVE_RETRY_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise last_exc


def ensure_worker_can_write(job, worker):
    """Raise :class:`JobWriteConflict` unless ``worker`` still owns ``job``.

    Call on the locked row.  A canceled, requeued, yielded or stuck-job
    reaped job fails here: it is no longer RENDERING, or no longer
    assigned to this worker.
    """
    if job.assigned_worker_id != worker.pk:
        raise JobWriteConflict(
            "Job is no longer assigned to this worker; "
            "the update was not applied."
        )
    if job.status != JobStatus.RENDERING:
        raise JobWriteConflict(
            f"Job is in '{job.status}' status, not RENDERING; "
            "the update was not applied."
        )


def ensure_valid_transition(job, new_status):
    """Raise :class:`JobWriteConflict` if ``job`` cannot move to ``new_status``.

    Checked against the locked row: the serializer validated the
    transition against the unlocked copy, which may be stale.
    """
    allowed = VALID_STATUS_TRANSITIONS.get(job.status, [])
    if new_status not in allowed:
        raise JobWriteConflict(
            f"Invalid status transition from {job.status} to {new_status}."
        )


def _locked_write(job_pk, apply):
    """Lock the job, ``apply(job)``, commit; retry only uncommitted attempts.

    ``apply`` re-checks the locked row (raising :class:`JobWriteConflict`)
    and saves it.  A sentinel registered as the first ``on_commit``
    callback records the commit.  If a later callback then raises (a
    thumbnail save that hits "database is locked", for example), the
    write itself succeeded: the error is logged and the committed job is
    returned, so the write is neither retried nor reported as failed.

    Returns:
        The locked and saved ``Job`` instance.
    """
    def _attempt():
        committed = []
        result = []
        try:
            with transaction.atomic():
                transaction.on_commit(lambda: committed.append(True))
                job = lock_own_rows(Job.objects).get(pk=job_pk)
                apply(job)
                result.append(job)
        except Exception:
            if not committed:
                raise
            logger.exception(
                "Job %s write committed, but a post-commit callback "
                "failed; not retrying the write.", job_pk,
            )
        return result[0]

    return run_with_lock_retry(_attempt, job_pk)


def _set_and_save(job, changes):
    for field, value in changes.items():
        setattr(job, field, value)
    job.save(update_fields=sorted(changes))


def apply_worker_patch(job_pk, worker, changes):
    """Apply a worker's validated PATCH ``changes`` to the locked job.

    Args:
        job_pk: Primary key of the job.
        worker: The calling ``Worker``.
        changes: Validated serializer data; keys must be in
            ``WORKER_PATCH_FIELDS``.

    Returns:
        The updated ``Job`` instance.

    Raises:
        JobWriteConflict: the job was canceled, requeued or reassigned, or
            the status change is not valid from its current status.
        ValueError: ``changes`` holds a field workers may not write.
    """
    disallowed = set(changes) - WORKER_PATCH_FIELDS
    if disallowed:
        raise ValueError(f"Workers cannot write {sorted(disallowed)}.")

    def _apply(job):
        ensure_worker_can_write(job, worker)
        if 'status' in changes:
            ensure_valid_transition(job, changes['status'])
        _set_and_save(job, changes)

    return _locked_write(job_pk, _apply)


def apply_admin_patch(job_pk, changes):
    """Apply an admin's validated PATCH/PUT ``changes`` to the locked job.

    Only the sent fields are written, so a worker report or claim that
    committed after the admin's read survives.  A requested status change
    is re-validated against the locked row.

    Raises:
        JobWriteConflict: the status change is no longer valid.
    """
    def _apply(job):
        if 'status' in changes:
            ensure_valid_transition(job, changes['status'])
        _set_and_save(job, changes)

    return _locked_write(job_pk, _apply)


def attach_worker_output(job_pk, worker, output_name, thumbnail_name=None):
    """Point the locked job at already-stored output (and thumbnail) files.

    Writes only ``output_file`` (and ``thumbnail`` when given).  Both are
    written in one ``save()`` so the ``post_save`` handler sees a
    worker-provided thumbnail and skips server-side generation.

    Returns:
        The updated ``Job`` instance.

    Raises:
        JobWriteConflict: the job no longer accepts output from ``worker``.
    """
    fields = {'output_file': output_name}
    if thumbnail_name:
        fields['thumbnail'] = thumbnail_name

    def _apply(job):
        ensure_worker_can_write(job, worker)
        _set_and_save(job, fields)

    return _locked_write(job_pk, _apply)
