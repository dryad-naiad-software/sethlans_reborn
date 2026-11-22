# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Storing a pilot's measurement (Unreal foundation FR-PM-4, steps 3-7).

Runs after the body passed ``PilotResultRequestSerializer`` (step 1) and
the job id parsed (step 2).  Under one transaction it write-locks the
**job** row only (SQLite write lock first, then ``FOR UPDATE`` of the job
row), and checks, in order: the caller owns the RENDERING job (409), the
renderer supports pilot measurement (409), the job is a pilot (409), the
job-dependent rules (400: engine build, the GPU cross-check against the
caller's own heartbeat reading, plausibility, the card kind and D6's
verdict), and the CVar hash (409, which also fails the pilot).  On
success the record's results are written by primary key with
``verdict_source='pilot'`` and the job becomes DONE.

**Locks (per-machine memory test D15):** the Job row, then the caller's
own Worker row (``FOR UPDATE``; one row only, so it cannot run against
the ascending order of ``worker_locks.lock_workers``, and nothing here
locks another Worker), then, in one ``ORDER BY pk`` ``FOR UPDATE`` query,
the pilot's record and the current records of the pair (worker, card
kind), any key and project, whose driver differs from the reported one
(none when the reported driver is null).  Records whose driver equals the
reported one, the common case, are neither locked nor written.  The
``Project`` row is never locked or written here.

**Invariant (see ``worker_locks``):** every path that locks a measurement
record or writes its ``current``, ``verdict`` or ``stale_reason`` holds
that record's Worker row first.  That is why D3 below may change records
of other projects without their ``lock_project``: the Worker row
serializes every writer of this pair's records.

**Driver (D3):** when a non-null reported driver differs from the
non-null driver of some other current record of the pair, **every**
current record of the pair whose driver is not the reported one, those
stored with '' (unknown) included, becomes
``stale_reason='driver_changed'``, whatever its verdict.  Without such a
record (all others unknown or equal) nothing is staled; a null driver
(UE's ``Unknown`` included) stales nothing.  D3 applies only while the
pilot's record is still current (and was locked): a superseded record
keeps its figures as history and changes no other record.  The record
locks and D3 are ``pair_locks.lock_pair`` and
``pair_locks.stale_older_drivers`` (shared with ``memory-report/``).  The
fits-nowhere check of the key is registered ``on_commit``
(``fits_nowhere.check_on_commit``).
"""

from django.db import transaction
from django.utils import timezone

from shared.unreal.vram_gate import fits_result, usable_mb

from ..constants import UnrealKind
from ..constants_sequence import CARD_KIND_KEY
from ..models import Job, JobStatus, UnrealMeasurement, Worker
from ..parent_status import take_write_lock
from ..renderers import UnsupportedRendererError, get_strategy
from ..renderers.unreal_machine import FITS, reading_measures_card, stored_card_kind
from .fits_nowhere import check_on_commit
from .pair_locks import lock_pair, stale_older_drivers
from .row_locks import lock_own_rows

NOT_RENDERING_HERE = "Job is not rendering on this worker."
KIND_UNSUPPORTED = "Pilot results are not supported for this renderer."
NOT_A_PILOT = "Job is not a pilot job."
CVAR_MISMATCH = "Pilot rendered different CVars than its measurement key."
CVAR_MISMATCH_ERROR = "Pilot render failed: it rendered different CVars than its measurement key."
GPU_MISMATCH = "Does not match this worker's reported GPU memory."
KIND_NOT_THE_JOBS = "Does not match the job's card kind."
KIND_NOT_THE_CARDS = "Does not match the kind of this worker's card gpu.card_key."
TOTAL_SKEW_MB = 64
_RESULT_FIELDS = (
    'peak_mb', 'peak_source', 'card_delta_peak_mb', 'baseline_used_mb', 'baseline_drift_mb',
    'peak_phase', 'max_sample_gap_s', 'prep_s', 'warmup_s', 'warmup_ran', 'render_s',
    'frames', 'seconds_per_frame', 'csv_local_used_peak_mb',
    # What the pilot ran with (gpu-memory-reader FR-GC-8); stored, never part of the key.
    'game_overrides', 'texture_streaming',
    # Stored as sent (per-card spec FR-PC-MGR-1); ``ram`` is written by ``_store``.
    'ram_process_commit_peak_mb', 'shared_machine',
)


def _plain_ram(ram):
    """The validated ``ram`` object as plain dicts for the JSON column (``None`` stays SQL NULL)."""
    if ram is None:
        return None
    return {phase: None if figures is None else dict(figures) for phase, figures in ram.items()}


def _gpu_matches(gpu_memory, gpu):
    """The reported GPU is in the caller's own live card reading (Sec R3-F1; FR-GH-10).

    Matched by ``card_key`` and total, never by ``index``: the index of one
    backend need not be another's (gpu-memory-reader FR-GN-5).
    """
    if not reading_measures_card(gpu_memory):
        return False
    for stored in gpu_memory.get('gpus') or []:
        if (isinstance(stored, dict) and stored.get('card_key') is not None
                and stored.get('card_key') == gpu['card_key']
                and isinstance(stored.get('total_mb'), int)
                and abs(stored['total_mb'] - gpu['total_mb']) <= TOTAL_SKEW_MB):
            return True
    return False


def _plausibility_errors(data, errors):
    card, csv = data['card_delta_peak_mb'], data['csv_local_used_peak_mb'] or 0
    if data['peak_mb'] != max(card, csv):
        errors['peak_mb'] = ["Must equal max(card_delta_peak_mb, csv_local_used_peak_mb)."]
    elif data['peak_source'] != ('csv' if csv > card else 'card'):
        errors['peak_source'] = ["Must name the larger of the card delta and the CSV peak."]
    if data['peak_mb'] + data['baseline_used_mb'] > data['gpu']['total_mb'] + TOTAL_SKEW_MB:
        errors.setdefault('peak_mb', []).append(
            "peak_mb + baseline_used_mb exceeds the card's total memory.")


def _card_kind_errors(job, worker, data, errors):
    """FR-MT-V-3: the kind is the job's ``unreal.card_kind`` and the kind of the caller's stored card."""
    settings = job.render_settings if isinstance(job.render_settings, dict) else {}
    if data['card_kind'] != settings.get(CARD_KIND_KEY):
        errors['card_kind'] = [KIND_NOT_THE_JOBS]
    elif data['card_kind'] != stored_card_kind(worker, data['gpu']['card_key']):
        errors['card_kind'] = [KIND_NOT_THE_CARDS]


def _verdict_errors(data, errors):
    """D6 recomputed: past the 93% line the verdict must be ``does_not_fit``.

    The manager cannot see the samples, so under the line either verdict is
    accepted: a saturated sample may only turn ``fits`` into ``does_not_fit``.
    """
    total = data['gpu']['total_mb']
    if data['verdict'] == FITS and not fits_result(data['peak_mb'], total, data['baseline_used_mb']):
        errors['verdict'] = [
            f"Must be does_not_fit: peak_mb + baseline_used_mb is over the card's 93% line "
            f"({usable_mb(total, 0)} MB)."]


def job_dependent_errors(job, worker, data):
    """Step 6: the 400 checks that need the job and the caller's stored reading."""
    errors = {}
    if data['engine_build'] != job.engine_build:
        errors['engine_build'] = ["Does not match the job's engine build."]
    if not _gpu_matches(worker.gpu_memory, data['gpu']):
        errors['gpu'] = [GPU_MISMATCH]
    _plausibility_errors(data, errors)
    _card_kind_errors(job, worker, data, errors)
    _verdict_errors(data, errors)
    return errors


def _gate_refusal(job, worker):
    """Steps 3-5: ``(status, body)`` of a 409, or ``None``."""
    if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
        return 409, {'error': NOT_RENDERING_HERE}
    try:
        supported = get_strategy(job.renderer).supports('pilot_measurement')
    except UnsupportedRendererError:
        supported = False
    if not supported:
        return 409, {'error': KIND_UNSUPPORTED}
    if job.unreal_kind != UnrealKind.PILOT:
        return 409, {'error': NOT_A_PILOT}
    return None


def _fail_pilot(job, now):
    job.status = JobStatus.ERROR
    job.error_message = CVAR_MISMATCH_ERROR
    job.completed_at = now
    job.save(update_fields=['status', 'error_message', 'completed_at'])


def _store(job, record, worker, data, now):
    results = {field: data[field] for field in _RESULT_FIELDS}
    gpu = data['gpu']
    driver = data['driver_version'] or ''
    rows = lock_pair(record, worker, driver)
    locked = next((row for row in rows if row.pk == record.pk), None)
    if locked is not None and locked.current:  # a record gone or superseded meanwhile: no D3
        stale_older_drivers(locked, rows, driver)
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        **results, verdict=data['verdict'], verdict_source='pilot', lower_bound_mb=data['lower_bound_mb'],
        shared_peak_mb=data['shared_peak_mb'], driver_version=driver,
        per_shot=[dict(shot) for shot in data['per_shot']], ram=_plain_ram(data['ram']),
        worker=worker, worker_hostname=worker.hostname, gpu_index=gpu['index'],
        gpu_name=gpu['name'], gpu_total_mb=gpu['total_mb'], measured_at=now,
    )
    job.status = JobStatus.DONE
    job.completed_at = now
    job.render_time_seconds = round(data['render_s'])
    job.last_output = data['summary']
    job.save(update_fields=['status', 'completed_at', 'render_time_seconds', 'last_output'])
    check_on_commit(record.project_id, record.asset_id, record.overrides_sha256, record.target_sha256)


def record_pilot_result(job_pk, worker, data):
    """Apply a validated ``pilot-result/`` body; return ``(status, body)``.

    Raises:
        Job.DoesNotExist: no such job (the view answers 404).
    """
    with transaction.atomic():
        # A write first: SQLite takes its write lock now (#225).
        take_write_lock(Job.objects.filter(pk=job_pk))
        job = lock_own_rows(Job.objects).get(pk=job_pk)
        refusal = _gate_refusal(job, worker)
        if refusal is not None:
            return refusal
        record = UnrealMeasurement.objects.filter(pilot_job_id=job.pk).first()
        if record is None:
            return 409, {'error': NOT_A_PILOT}
        # D15: the caller's own Worker row after the Job row, before any record lock.
        worker = lock_own_rows(Worker.objects).get(pk=worker.pk)
        errors = job_dependent_errors(job, worker, data)
        if errors:
            return 400, errors
        now = timezone.now()
        if data['overrides_sha256'] != record.overrides_sha256:
            _fail_pilot(job, now)
            return 409, {'error': CVAR_MISMATCH}
        _store(job, record, worker, data, now)
    return 200, {'status': JobStatus.DONE, 'peak_mb': data['peak_mb']}
