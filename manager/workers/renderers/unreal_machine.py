# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-machine Unreal memory results (Unreal per-machine memory test D1, D2,
D4, D5, D10, D14, FR-MT-K-3, FR-MT-T-1).

``worker_kinds(worker)`` gives the card kinds a worker is eligible for:
the first ``MAX_KINDS_PER_WORKER`` sorted distinct kinds of the cards in
its stored ``card_status.cards`` that are ``ticked`` true, in state
``ok`` and have a kind (a vendor and a device id).  The stored report is
self-reported and already reduced by ``views.heartbeat_cards``; anything
not in that shape counts as no card here.

A **pair** is (key, worker, card kind); its current record gives its
state (D2) as one SQL ``Case`` (:func:`pair_state_case`).  The poll
(:func:`machine_poll_q`) offers a render job only to a worker with a
current, not stale, ``fits`` record of the job's key for one of its kinds
(:func:`fits_exists`), an audio job the same for its submission's piece
key (D10), a pilot only to its target worker, and ``shots`` jobs to every
worker.  A worker without a live card reading has no kinds (D14).
"""

from django.db.models import Case, Exists, F, OuterRef, Q, TextField, Value, When

from shared.unreal.card_kind import card_kind

from ..constants import UnrealKind
from ..models import JobStatus, UnrealMeasurement
from ..models.workers import reports_share_reachable

# D5: at most this many kinds per worker, so a changing revision id cannot
# grow the number of pilots a worker gets.
MAX_KINDS_PER_WORKER = 4
_OK = 'ok'
_ACTIVE = (JobStatus.QUEUED, JobStatus.RENDERING)

# D2 pair states.
FITS, DOES_NOT_FIT, TESTING = 'fits', 'does_not_fit', 'testing'
FAILED, RETEST_DUE, NOT_TRIED = 'failed', 'retest_due', 'not_tried'

# References from a Job query to its own key columns (render jobs), and to
# its submission's piece key (audio jobs, D10).
JOB_KEY_REFS = (
    OuterRef('asset_id'),
    OuterRef('asset__project__unreal_overrides_sha256'),
    OuterRef('unreal_target_sha256'),
)
AUDIO_KEY_REFS = (
    OuterRef('asset_id'),
    OuterRef('asset__project__unreal_overrides_sha256'),
    OuterRef('sequence_submission__piece_target_sha256'),
)
PIECE_TARGET = '_unreal_piece_target'


def _card_kind_of(card):
    """The D1 kind of a stored card that is ticked and ``ok``, else ``None``."""
    if not isinstance(card, dict) or card.get('ticked') is not True or card.get('state') != _OK:
        return None
    return card_kind(card.get('vendor_id'), card.get('device_id'), card.get('revision_id'))


def worker_kinds(worker) -> tuple:
    """The first four sorted distinct kinds of the worker's ticked ``ok`` cards (D5)."""
    status = worker.card_status
    cards = status.get('cards') if isinstance(status, dict) else None
    if not isinstance(cards, list):
        return ()
    kinds = {_card_kind_of(card) for card in cards} - {None}
    return tuple(sorted(kinds)[:MAX_KINDS_PER_WORKER])


def reading_measures_card(gpu_memory):
    """Whether a stored ``gpu_memory`` is a live card reading (gpu-memory-reader D7, FR-GH-4).

    True only when ``capabilities.card is True`` as the heartbeat stored it
    (FR-GH-2 forces it false for the override and for an empty reading).
    """
    reading = gpu_memory if isinstance(gpu_memory, dict) else {}
    capabilities = reading.get('capabilities')
    return isinstance(capabilities, dict) and capabilities.get('card') is True


def measures_card(worker):
    """Whether the worker's stored ``gpu_memory`` is a live card reading (FR-GH-4)."""
    return reading_measures_card(worker.gpu_memory)


def eligible_kinds(worker) -> tuple:
    """The worker's kinds, or none without a live card reading (D14)."""
    return worker_kinds(worker) if measures_card(worker) else ()


def serves_build(worker, build, share) -> bool:
    """D5: the worker reports ``build`` and the manager's ``share`` as reachable (activity and age aside)."""
    return bool(share) and worker.has_unreal_build(build) and reports_share_reachable(worker.unreal_capabilities, share)


def current_records(asset, overrides, target):
    """The current records of one key; each argument is a value or an ``OuterRef``."""
    return UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=overrides, target_sha256=target, current=True,
    )


def fits_records(refs):
    """The key's current, not stale, ``fits`` records (any worker)."""
    return current_records(*refs).filter(verdict=FITS, stale_reason='')


def fits_exists(refs, worker, kinds):
    """``Exists`` of a fits record of the key for ``worker`` and one of ``kinds`` (FR-MT-T-1)."""
    return Exists(fits_records(refs).filter(worker=worker, card_kind__in=list(kinds)))


def pair_state_case(prefix=''):
    """The D2 state of a current record (or one reached through ``prefix``)."""
    return Case(
        When(~Q(**{f'{prefix}stale_reason': ''}), then=Value(RETEST_DUE)),
        When(**{f'{prefix}verdict': FITS}, then=Value(FITS)),
        When(**{f'{prefix}verdict': DOES_NOT_FIT}, then=Value(DOES_NOT_FIT)),
        When(**{f'{prefix}pilot_job__status__in': _ACTIVE}, then=Value(TESTING)),
        default=Value(FAILED), output_field=TextField(),
    )


def live_pilot():
    """A pilot whose record is current and was created under the project's current CVars."""
    return Exists(UnrealMeasurement.objects.filter(
        pilot_job=OuterRef('pk'), current=True,
        overrides_sha256=OuterRef('asset__project__unreal_overrides_sha256'),
    ))


def machine_poll_q(worker):
    """FR-MT-T-1: the Unreal kinds a poll offers ``worker`` (``None``: a non-worker caller)."""
    shots = Q(unreal_kind=UnrealKind.SHOTS)
    pilot = Q(unreal_kind=UnrealKind.PILOT) & live_pilot()
    if worker is None:
        return (shots
                | (Q(unreal_kind=UnrealKind.RENDER) & Exists(fits_records(JOB_KEY_REFS)))
                | (Q(unreal_kind=UnrealKind.AUDIO) & Exists(fits_records(AUDIO_KEY_REFS)))
                | (pilot & Q(target_worker__isnull=False)))
    kinds = eligible_kinds(worker)
    if not kinds:
        return shots
    return (shots
            | (Q(unreal_kind=UnrealKind.RENDER) & fits_exists(JOB_KEY_REFS, worker, kinds))
            | (Q(unreal_kind=UnrealKind.AUDIO) & fits_exists(AUDIO_KEY_REFS, worker, kinds))
            | (pilot & Q(target_worker=worker)))


def piece_target_annotation():
    """``annotate()`` kwargs: an audio job's piece key target, read with the job (no query per row)."""
    return {PIECE_TARGET: F('sequence_submission__piece_target_sha256')}
