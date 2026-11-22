# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The effects of an accepted memory report (Unreal per-machine memory test
D2, D6, D9, FR-MT-S-3), called by ``memory_report`` with the Job, the
caller's Worker row and the records locked.

* :func:`feed_back` -- D6: the report's ``job_peak_mb`` raises the
  record's ``job_peak_max_mb`` when larger; the record becomes
  ``does_not_fit`` on a ``self`` spill (``verdict_source='spill'``) or when
  ``result peak + baseline_used_mb > floor(total x 0.93)``
  (``'job_peak'``), with ``lower_bound_mb = max(result peak, job_peak_mb)``.
  The update is ``WHERE pk AND current AND verdict='fits'``; for a derived
  record its current ``fits`` covering record becomes ``does_not_fit`` too.
* :func:`add_warning` -- D9: a piece's or audio job's spill adds one
  warning to its version (the submission row is locked here, after the
  records).
* :func:`pair_state` -- the D2 state of the record's pair.
"""

import logging

from django.db.models import Q

from shared.unreal.shots_result import clean_text
from shared.unreal.vram_gate import fits_result

from ..constants_sequence import MAX_SUBMISSION_WARNINGS
from ..models import SequenceSubmission, UnrealMeasurement
from ..parent_status import lock_parent
from ..renderers.unreal_machine import DOES_NOT_FIT, FITS, NOT_TRIED, current_records, pair_state_case
from ..serializers.memory_report import SPILL_CONTENTION, SPILL_SELF

logger = logging.getLogger(__name__)

SPILL_WARNING = ("Machine {host} ran out of graphics card memory on {job} ({card}); "
                 "the rest of this version goes to cards where it fits.")
CONTENTION_WARNING = ("Machine {host}'s graphics card memory was squeezed by another program during "
                      "{job} ({card}); the render finished.")
_STILL_FITS = Q(current=True, verdict=FITS)


def _result_peak(record):
    """D2: ``max(peak_mb, job_peak_max_mb or 0)``."""
    return max(record.peak_mb or 0, record.job_peak_max_mb or 0)


def _over_line(peak, record):
    """D6: the result peak plus the pilot's baseline is over the card's 93% line (unknown figures: no)."""
    total, baseline = record.gpu_total_mb, record.baseline_used_mb
    if not total or baseline is None:
        return False
    return not fits_result(peak, total, baseline)


def _mark_covering(covering, source, bound):
    """A derived record's covering record ``covering`` becomes ``does_not_fit`` while current and fits."""
    lower = max(_result_peak(covering), bound)
    changed = UnrealMeasurement.objects.filter(_STILL_FITS, pk=covering.pk).update(
        verdict=DOES_NOT_FIT, verdict_source=source, lower_bound_mb=lower)
    return changed > 0


def _updates(record, data):
    """``(updates, source)``: the raised job peak and, past D6's line or on a ``self`` spill, the verdict."""
    job_peak = data['job_peak_mb']
    raised = record.job_peak_max_mb
    if job_peak is not None and job_peak > (raised or 0):
        raised = job_peak
    peak = max(record.peak_mb or 0, raised or 0)
    source = None
    if data['spill_cause'] == SPILL_SELF:
        source = 'spill'
    elif _over_line(peak, record):
        source = 'job_peak'
    updates = {} if raised == record.job_peak_max_mb else {'job_peak_max_mb': raised}
    if source is not None:
        updates.update(verdict=DOES_NOT_FIT, verdict_source=source, lower_bound_mb=max(peak, job_peak or 0))
    return updates, source


def feed_back(record, covering, data):
    """Step (3) of FR-MT-S-3 on the locked current ``record``.

    ``covering`` is its locked covering record of the same pair, or ``None``.
    Returns ``(record verdict changed, covering verdict changed)``.
    """
    updates, source = _updates(record, data)
    if not updates or not UnrealMeasurement.objects.filter(_STILL_FITS, pk=record.pk).update(**updates):
        return False, False
    if source is None:
        return False, False
    logger.info("Worker %s, card kind %s: result %s does not fit any more (%s, at least %s MB).",
                record.worker_hostname, record.card_kind, record.pk, source, updates['lower_bound_mb'])
    return True, covering is not None and _mark_covering(covering, source, updates['lower_bound_mb'])


def _card_name(worker, card_key, record):
    """The stored name of the caller's card ``card_key``, else the record's GPU name, else the key."""
    status = worker.card_status
    cards = status.get('cards') if isinstance(status, dict) else None
    for card in cards if isinstance(cards, list) else ():
        if isinstance(card, dict) and card.get('key') == card_key and card.get('name') and isinstance(card['name'], str):
            return card['name']
    return record.gpu_name or card_key


def _warning_text(job, worker, data, record):
    template = {SPILL_SELF: SPILL_WARNING, SPILL_CONTENTION: CONTENTION_WARNING}.get(data['spill_cause'])
    if data['spilled'] is not True or template is None:
        return None
    card = _card_name(worker, data['card_key'], record)
    return clean_text(template.format(host=worker.hostname, job=job.name, card=card))


def add_warning(job, worker, data, record):
    """Step (4): a piece's or audio job's spill adds its warning to the version once."""
    text = _warning_text(job, worker, data, record)
    if text is None or job.sequence_submission_id is None:
        return
    submission = lock_parent(SequenceSubmission, job.sequence_submission_id)
    if submission is None:
        return
    warnings = list(submission.warnings or [])
    if text in warnings:
        return
    # At the cap the older warnings give way (``sound_runs.merged_warnings``), so this one is kept.
    SequenceSubmission.objects.filter(pk=submission.pk).update(
        warnings=(warnings + [text])[-MAX_SUBMISSION_WARNINGS:])


def pair_state(record, worker):
    """The D2 state of ``record``'s pair (its key, the caller, its kind) now; ``not_tried`` without one."""
    state = current_records(record.asset_id, record.overrides_sha256, record.target_sha256).filter(
        worker_id=worker.pk, card_kind=record.card_kind,
    ).annotate(_pair_state=pair_state_case()).order_by('-pk').values_list('_pair_state', flat=True).first()
    return state or NOT_TRIED
