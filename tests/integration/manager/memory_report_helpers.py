# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the per-machine memory test chunk 6 integration tests
(Unreal per-machine memory test FR-MT-S-3, D3, D6, D9: ``POST
/api/jobs/{id}/memory-report/``).  Not a test module.

:func:`rendering_render` gives a standalone Unreal render job RENDERING
on the caller, gated by a current ``fits`` record of the caller's card
kind ``KIND`` on a 32,607 MB card with a 945 MB baseline (D6's line is
``machine_verdict_helpers.LINE``) and the driver :data:`OLD_DRIVER`.
:func:`report_body` is a report that passes every rule for that record
and changes nothing: no spill, a job peak below the record's peak and the
record's own driver.
"""

from workers.models import Job, SequenceSubmission, UnrealMeasurement

from .machine_test_helpers import pair_record, render_job
from .machine_verdict_helpers import BASELINE, OLD_DRIVER, TOTAL
from .unreal_helpers import card_key_of
from .unreal_pilot_helpers import JOBS, KIND, claim

CARD_KEY = card_key_of(0)
# The stored name of ``ready_worker``'s card (``card_report_helpers.card()``).
CARD_NAME = 'NVIDIA GeForce RTX 5090'
NOT_RENDERING = "Job is not rendering on this worker."
NOT_KEYED = "Memory reports are only for Unreal render and audio jobs."
ALREADY = "A different memory report was already accepted for this job."
KIND_MISMATCH = "Does not match the kind of this worker's card card_key."
NOT_THE_RECORD = "Not a memory result of this worker, card kind and job."
BODY_KEYS = ('measurement_id', 'card_kind', 'card_key', 'driver_version', 'spilled', 'spill_cause',
             'shared_peak_mb', 'job_peak_mb')


def spill_text(host, job_name, card=CARD_NAME):
    """FR-MT-S-3's ``self`` spill warning, word for word."""
    return (f"Machine {host} ran out of graphics card memory on {job_name} ({card}); "
            f"the rest of this version goes to cards where it fits.")


def contention_text(host, job_name, card=CARD_NAME):
    """FR-MT-S-3's ``contention`` warning, word for word."""
    return (f"Machine {host}'s graphics card memory was squeezed by another program during "
            f"{job_name} ({card}); the render finished.")


def report_body(record, **overrides):
    """A ``memory-report/`` body for ``record`` on card 0 that changes nothing by itself."""
    body = {
        'measurement_id': record.pk, 'card_kind': record.card_kind or KIND, 'card_key': CARD_KEY,
        'driver_version': record.driver_version or None, 'spilled': False, 'spill_cause': None,
        'shared_peak_mb': 96, 'job_peak_mb': 4000,
    }
    body.update(overrides)
    return body


def self_spill(record, **overrides):
    return report_body(record, spilled=True, spill_cause='self', shared_peak_mb=1900, **overrides)


def contention(record, **overrides):
    return report_body(record, spilled=True, spill_cause='contention', shared_peak_mb=1300, **overrides)


def post_report(client, job, body):
    pk = job if isinstance(job, (int, str)) else job.pk
    return client.post(f'{JOBS}{pk}/memory-report/', body, format='json')


def accepted(client, job, body, result='fits'):
    """POST ``body``; assert ``200 {"result": result}``."""
    resp = post_report(client, job, body)
    assert (resp.status_code, resp.data) == (200, {'result': result}), resp.data
    return resp


def keyed_record(asset, target_hash, worker, kind=KIND, **fields):
    """A current ``fits`` record of the pair with the card figures D6 needs and :data:`OLD_DRIVER`."""
    values = dict(verdict='fits', driver_version=OLD_DRIVER, gpu_total_mb=TOTAL, baseline_used_mb=BASELINE,
                  gpu_name='Example GPU')
    values.update(fields)
    return pair_record(asset, target_hash, worker, kind, **values)


def rendering_render(asset, worker, client, name='MtReport', render_settings=None, **fields):
    """A render job claimed by ``worker`` on its ``keyed_record``; ``(job, record)``."""
    job = render_job(asset, name=name, render_settings=render_settings)
    record = keyed_record(asset, job.unreal_target_sha256, worker, **fields)
    resp = claim(client, job, worker)
    assert resp.status_code == 200, resp.data
    job.refresh_from_db()
    return job, record


def derive_from(record, covering):
    """Make ``record`` a derived record of ``covering`` (D8: ``verdict_source='covered'``)."""
    UnrealMeasurement.objects.filter(pk=record.pk).update(verdict_source='covered', covered_by=covering)
    record.refresh_from_db()
    return record


def stored_report(job):
    return Job.objects.values_list('unreal_memory_report', flat=True).get(pk=job.pk)


def state(record):
    """``(verdict, verdict_source, stale_reason, job_peak_max_mb, lower_bound_mb, driver_version, current)``."""
    return UnrealMeasurement.objects.values_list(
        'verdict', 'verdict_source', 'stale_reason', 'job_peak_max_mb', 'lower_bound_mb', 'driver_version',
        'current').get(pk=record.pk)


def piece_record(submission, worker, kind=KIND):
    """The worker's current record of the submission's piece key."""
    return UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker,
                                         card_kind=kind, current=True)


def warnings_of(submission):
    return list(SequenceSubmission.objects.values_list('warnings', flat=True).get(pk=submission.pk) or [])
