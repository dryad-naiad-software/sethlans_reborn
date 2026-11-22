# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The measured gate in SQL (Unreal foundation FR-PM-1, FR-MS-5, FR-RT-4,
FR-RT-9; per-machine memory test D2, FR-MT-T-1..3).

A normal Unreal job's **measurement key** is ``(asset, the project's
unreal_overrides_sha256, the job's unreal_target_sha256)``; an audio
job's is its submission's piece key (D10).  A result belongs to one key,
one worker and one card kind; the **pair** states are in
``renderers.unreal_machine``.  The poll and the claim judge the caller's
own pairs only: a render or audio job goes to a worker with a current,
not stale, ``fits`` record of the key for one of its card kinds; a pilot
goes to its target worker while its record is current and fresh.

For the routing note and the admin retry the key also has a farm-wide
state, one ``Case`` over its current records (:func:`key_state_case`):
**measured** when some pair fits, else **pending** while some pair's
pilot is QUEUED or RENDERING, else **failed** when some pair's test
failed (pilot ERROR, CANCELED, DONE without a result, or deleted), else
**does_not_fit** when some pair does not fit, else **none**.  Blender rows
(empty target hash) evaluate to ``NULL``: no record has an empty hash.
"""

from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, TextField, Value, When

from ..constants import UnrealKind
from ..models import Job, JobStatus, UnrealMeasurement
from .unreal_machine import (
    DOES_NOT_FIT, FITS, RETEST_DUE, TESTING, current_records, eligible_kinds, measures_card, pair_state_case,
)

MEASURED, PENDING, FAILED, NONE = 'measured', 'pending', 'failed', 'none'
TOO_BIG = 'does_not_fit'
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
TOO_BIG_NOTE = "This render does not fit the graphics cards of any machine that tested it."
NO_CARD_READING_MESSAGE = "Worker cannot measure GPU memory (no live card reading)."
NO_KIND_MESSAGE = "This machine has no ticked graphics card with a known PCI id."
STALE_MESSAGE = "Pilot is stale: the project's CVars changed since it was created."
SUPERSEDED_MESSAGE = "Pilot is no longer needed: its target was measured by a later pilot."
NOT_AVAILABLE_MESSAGE = "Job is not available for claiming."
NOT_TESTED_MESSAGE = "This machine has not tested this render yet."
DOES_NOT_FIT_MESSAGE = "This render does not fit this machine's graphics cards."
RETEST_MESSAGE = "This machine's memory test is being re-done after a driver change."
MAX_REASON = 200


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
    if state == TOO_BIG:
        return TOO_BIG_NOTE
    return None


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
    """``alias()`` kwargs reading one key's current records (one statement with the rest)."""
    records = current_records(*refs)
    untested = records.filter(verdict='')
    return {
        f'{prefix}fits': Exists(records.filter(verdict=FITS, stale_reason='')),
        f'{prefix}testing': Exists(untested.filter(pilot_job__status__in=_ACTIVE)),
        f'{prefix}failed': Exists(untested.filter(stale_reason='').exclude(pilot_job__status__in=_ACTIVE)),
        f'{prefix}too_big': Exists(records.filter(verdict=DOES_NOT_FIT, stale_reason='')),
    }


def key_state_case(prefix, blender=None):
    """The farm-wide key state ``Case`` over :func:`key_aliases`; ``blender`` gives NULL."""
    whens = [] if blender is None else [When(blender, then=Value(None))]
    whens += [
        When(**{f'{prefix}fits': True}, then=Value(MEASURED)),
        When(**{f'{prefix}testing': True}, then=Value(PENDING)),
        When(**{f'{prefix}failed': True}, then=Value(FAILED)),
        When(**{f'{prefix}too_big': True}, then=Value(TOO_BIG)),
    ]
    return Case(*whens, default=Value(NONE), output_field=TextField())


def key_reason(refs):
    """The failure reason of the key's latest current record without a verdict."""
    records = _latest(current_records(*refs).filter(verdict='', stale_reason='')).annotate(_reason=record_reason())
    return Subquery(records.values('_reason')[:1])


def note_annotations(queryset):
    """Add ``_unreal_key_state`` and ``_unreal_key_reason`` for the routing note (no query per row).

    An audio job reads its submission's piece key (D10).  Not added to
    worker polls: their routing note is always null.
    """
    target = Case(When(unreal_kind=UnrealKind.AUDIO, then=F('sequence_submission__piece_target_sha256')),
                  default=F('unreal_target_sha256'))
    refs = (OuterRef('asset_id'), OuterRef('asset__project__unreal_overrides_sha256'), OuterRef('_umk_target'))
    return queryset.alias(_umk_target=target).alias(**key_aliases(refs, '_umk_')).annotate(
        _unreal_key_state=key_state_case('_umk_', Q(unreal_target_sha256='')),
        _unreal_key_reason=key_reason(refs),
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


def _current():
    """A pilot whose record is its pair's current record (a later pilot of the pair supersedes it)."""
    return Exists(UnrealMeasurement.objects.filter(pilot_job=OuterRef('pk'), current=True))


def claim_row(job):
    """Read a job's pilot flags and key columns in one statement (FR-RT-4, FR-MT-T-2)."""
    return Job.objects.filter(pk=job.pk).annotate(_fresh=_fresh(), _current=_current()).values(
        '_fresh', '_current', 'asset_id', 'asset__project_id', 'asset__project__unreal_overrides_sha256',
        'unreal_target_sha256', 'sequence_submission__piece_target_sha256',
    ).first()


def _pair_states(worker, kinds, row, is_audio):
    """``{kind: (state, failure reason)}`` of the caller's current records of the job's key (one query)."""
    target = row['sequence_submission__piece_target_sha256'] if is_audio else row['unreal_target_sha256']
    records = current_records(
        row['asset_id'], row['asset__project__unreal_overrides_sha256'], target or '',
    ).filter(worker=worker, card_kind__in=list(kinds)).annotate(_state=pair_state_case(), _reason=record_reason())
    return {kind: (state, reason) for kind, state, reason in records.values_list('card_kind', '_state', '_reason')}


def pair_claim_rejection(worker, job, row):
    """FR-MT-T-2: why ``worker`` has no fits pair for the render or audio ``job``, or ``None``."""
    if not measures_card(worker):
        return NO_CARD_READING_MESSAGE
    kinds = eligible_kinds(worker)
    if not kinds:
        return NO_KIND_MESSAGE
    states = _pair_states(worker, kinds, row, job.unreal_kind == UnrealKind.AUDIO)
    found = {state for state, _reason in states.values()}
    if FITS in found:
        return None
    if RETEST_DUE in found:
        return RETEST_MESSAGE
    if TESTING in found or len(states) < len(kinds):
        return NOT_TESTED_MESSAGE
    reasons = [reason for state, reason in states.values() if state == FAILED]
    if reasons:
        return failed_note(reasons[0], row['asset__project_id'])
    return DOES_NOT_FIT_MESSAGE


def measured_claim_rejection(worker, job):
    """The measured-gate part of an Unreal claim check, or ``None`` (FR-RT-4, FR-MT-T-2).

    Never skipped: a job row that cannot be read is refused.
    """
    is_pilot = job.unreal_kind == UnrealKind.PILOT
    if is_pilot and not measures_card(worker):
        return NO_CARD_READING_MESSAGE
    row = claim_row(job)
    if row is None:
        return NOT_AVAILABLE_MESSAGE
    if is_pilot:
        if not row['_fresh']:
            return STALE_MESSAGE
        return None if row['_current'] else SUPERSEDED_MESSAGE
    return pair_claim_rejection(worker, job, row)
