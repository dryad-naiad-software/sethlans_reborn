# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The measured-peak gate in SQL (Unreal foundation FR-PM-1, FR-PM-5,
FR-MS-5, FR-RT-4, FR-RT-9).

A normal Unreal job's **measurement key** is ``(asset, the project's
unreal_overrides_sha256, the job's unreal_target_sha256)``.  Its state:

* **measured** -- some record of the key has a ``peak_mb`` (the latest
  such record's peak applies);
* otherwise the state of the key's latest record (``created_at``, ``id``):
  **pending** while its pilot is QUEUED or RENDERING, else **failed**
  (pilot ERROR, CANCELED, DONE without a result, or deleted);
* **none** when the key has no record.

The state is always one ``Case`` expression evaluated in one statement,
never two reads a concurrent commit could fall between.  ``poll_q``, the
claim check, the routing note and the job annotations all use the
expressions here, so the four cannot drift.  Blender rows (empty target
hash) evaluate to ``NULL``: no record has an empty hash.
"""

from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, TextField, Value, When

from ..constants import UnrealKind
from ..models import Job, JobStatus, UnrealMeasurement

MEASURED, PENDING, FAILED, NONE = 'measured', 'pending', 'failed', 'none'
_ACTIVE = (JobStatus.QUEUED, JobStatus.RENDERING)

CANCELED_REASON = "Pilot job was canceled."
DONE_REASON = "Pilot job finished without a measurement."
DELETED_REASON = "Pilot job was deleted."
REQUEUE_LIMIT_REASON = "Max auto-requeue attempts exceeded (3)"
INFRA_SENTENCE = (
    " The pilot was released or requeued too often; this points to a worker "
    "or network problem, not to the project."
)
PENDING_NOTE = "Waiting for this project's memory measurement (pilot render)."
NO_NVIDIA_MESSAGE = "Worker cannot measure GPU memory (no nvidia-smi reading)."
STALE_MESSAGE = "Pilot is stale: the project's CVars changed since it was created."
SUPERSEDED_MESSAGE = "Pilot is no longer needed: its target was measured by a later pilot."
MAX_REASON = 200

# References from a Job query to its own key columns.
JOB_KEY_REFS = (
    OuterRef('asset_id'),
    OuterRef('asset__project__unreal_overrides_sha256'),
    OuterRef('unreal_target_sha256'),
)


def retry_url(project_id):
    return f"POST /api/projects/{project_id}/unreal-measurement/retry/"


def failed_note(reason, project_id):
    """FR-RT-9 note 3a for a failed key (with the infrastructure sentence)."""
    reason = (reason or '')[:MAX_REASON]
    note = (
        f"This project's memory measurement failed: {reason}. "
        f"An admin can retry it ({retry_url(project_id)})."
    )
    if reason == REQUEUE_LIMIT_REASON:
        note += INFRA_SENTENCE
    return note


def none_note(project_id):
    """FR-RT-9 note 3a for a key with no record."""
    return (
        "No memory measurement exists for this project version; an admin can "
        f"start one ({retry_url(project_id)})."
    )


def held_message(state, reason, project_id):
    """The note / claim text for a held normal job, or ``None`` when measured."""
    if state == PENDING:
        return PENDING_NOTE
    if state == FAILED:
        return failed_note(reason, project_id)
    if state == NONE:
        return none_note(project_id)
    return None


def key_records(asset, overrides, target):
    """Records of one key; each argument is a value or an ``OuterRef``."""
    return UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=overrides, target_sha256=target,
    )


def _latest(records):
    return records.order_by('-created_at', '-id')


def record_reason(prefix=''):
    """The failure reason of a record (or a record reached through ``prefix``)."""
    status = f'{prefix}pilot_job__status'
    return Case(
        When(**{f'{prefix}pilot_job__isnull': True}, then=Value(DELETED_REASON)),
        When(**{status: JobStatus.ERROR}, then=F(f'{prefix}pilot_job__error_message')),
        When(**{status: JobStatus.CANCELED}, then=Value(CANCELED_REASON)),
        When(**{status: JobStatus.DONE}, then=Value(DONE_REASON)),
        default=Value(None), output_field=TextField(),
    )


def record_state():
    """The state of one record: measured, pending or failed (FR-PM-1)."""
    return Case(
        When(peak_mb__isnull=False, then=Value(MEASURED)),
        When(pilot_job__status__in=_ACTIVE, then=Value(PENDING)),
        default=Value(FAILED), output_field=TextField(),
    )


def key_aliases(refs, prefix):
    """``alias()`` kwargs reading one key's records (one statement with the rest)."""
    records = key_records(*refs)
    return {
        f'{prefix}success': Exists(records.filter(peak_mb__isnull=False)),
        f'{prefix}any': Exists(records),
        f'{prefix}latest_status': Subquery(_latest(records).values('pilot_job__status')[:1]),
    }


def key_state_case(prefix, blender=None):
    """The key state ``Case`` over :func:`key_aliases`; ``blender`` gives NULL."""
    whens = [] if blender is None else [When(blender, then=Value(None))]
    whens += [
        When(**{f'{prefix}success': True}, then=Value(MEASURED)),
        When(**{f'{prefix}any': False}, then=Value(NONE)),
        When(**{f'{prefix}latest_status__in': _ACTIVE}, then=Value(PENDING)),
    ]
    return Case(*whens, default=Value(FAILED), output_field=TextField())


def key_peak(refs):
    """``peak_mb`` of the key's latest successful record."""
    records = key_records(*refs).filter(peak_mb__isnull=False)
    return Subquery(_latest(records).values('peak_mb')[:1])


def key_reason(refs):
    """The failure reason of the key's latest record."""
    records = _latest(key_records(*refs)).annotate(_reason=record_reason())
    return Subquery(records.values('_reason')[:1])


def job_annotations(queryset):
    """Add ``_unreal_peak_mb``, ``_unreal_key_state``, ``_unreal_key_reason`` (FR-PM-5).

    Subqueries of the job query itself: a list adds no query.
    """
    blender = Q(unreal_target_sha256='')
    return queryset.alias(**key_aliases(JOB_KEY_REFS, '_umk_')).annotate(
        _unreal_peak_mb=key_peak(JOB_KEY_REFS),
        _unreal_key_state=key_state_case('_umk_', blender),
        _unreal_key_reason=key_reason(JOB_KEY_REFS),
    )


def _fresh():
    """A pilot whose record was created under the project's current CVars.

    A pilot without it is **stale** (FR-MS-5): it stays QUEUED and becomes
    eligible again if the CVars return to its key.
    """
    return Exists(UnrealMeasurement.objects.filter(
        pilot_job=OuterRef('pk'),
        overrides_sha256=OuterRef('asset__project__unreal_overrides_sha256'),
    ))


def _superseded():
    """A pilot whose key was measured after its own record was created."""
    own_created = UnrealMeasurement.objects.filter(
        pilot_job=OuterRef(OuterRef('pk')),
    ).values('created_at')[:1]
    later = key_records(*JOB_KEY_REFS).filter(
        peak_mb__isnull=False, measured_at__gt=Subquery(own_created),
    )
    return Exists(later)


def poll_measured_q(include_pilots):
    """FR-MS-5: normal jobs of measured keys; fresh, unsuperseded pilots."""
    normal = Q(unreal_kind=UnrealKind.RENDER) & Exists(
        key_records(*JOB_KEY_REFS).filter(peak_mb__isnull=False),
    )
    if not include_pilots:
        return normal
    pilot = Q(unreal_kind=UnrealKind.PILOT) & _fresh() & ~_superseded()
    return normal | pilot


def claim_row(job):
    """Read a job's key state and pilot flags in one statement (FR-RT-4)."""
    queryset = job_annotations(Job.objects.filter(pk=job.pk)).annotate(
        _fresh=_fresh(), _superseded=_superseded(),
    )
    return queryset.values(
        '_unreal_key_state', '_unreal_key_reason', '_unreal_peak_mb',
        '_fresh', '_superseded', 'asset__project_id',
    ).first()


def has_nvidia_reading(worker):
    """Whether the worker's stored ``gpu_memory`` comes from ``nvidia-smi`` (FR-WD-10)."""
    reading = worker.gpu_memory if isinstance(worker.gpu_memory, dict) else {}
    return reading.get('source') == 'nvidia-smi'


def measured_claim_rejection(worker, job):
    """The measured-gate part of an Unreal claim check, or ``None`` (FR-RT-4)."""
    is_pilot = job.unreal_kind == UnrealKind.PILOT
    if is_pilot and not has_nvidia_reading(worker):
        return NO_NVIDIA_MESSAGE
    row = claim_row(job)
    if row is None:
        return None
    # The claim response then reads these instead of querying again.
    job._unreal_key_state = row['_unreal_key_state']
    job._unreal_key_reason = row['_unreal_key_reason']
    job._unreal_peak_mb = row['_unreal_peak_mb']
    if is_pilot:
        if not row['_fresh']:
            return STALE_MESSAGE
        return SUPERSEDED_MESSAGE if row['_superseded'] else None
    return held_message(row['_unreal_key_state'], row['_unreal_key_reason'], row['asset__project_id'])
