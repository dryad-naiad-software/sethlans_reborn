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
caller's own heartbeat reading, plausibility), and the CVar hash (409,
which also fails the pilot).  On success the record's results are
written by primary key and the job becomes DONE.  The ``Project`` row is
never locked or written here (lock order Job -> UnrealMeasurement).
"""

from django.db import transaction
from django.utils import timezone

from shared.unreal.vram_gate import measured_need_mb

from ..constants import UnrealKind
from ..models import Job, JobStatus, UnrealMeasurement
from ..parent_status import take_write_lock
from ..renderers import UnsupportedRendererError, get_strategy
from .row_locks import lock_own_rows

NOT_RENDERING_HERE = "Job is not rendering on this worker."
KIND_UNSUPPORTED = "Pilot results are not supported for this renderer."
NOT_A_PILOT = "Job is not a pilot job."
CVAR_MISMATCH = "Pilot rendered different CVars than its measurement key."
CVAR_MISMATCH_ERROR = "Pilot render failed: it rendered different CVars than its measurement key."
GPU_MISMATCH = "Does not match this worker's reported GPU memory."
TOTAL_SKEW_MB = 64
_RESULT_FIELDS = (
    'peak_mb', 'peak_source', 'card_delta_peak_mb', 'baseline_used_mb', 'baseline_drift_mb',
    'peak_phase', 'max_sample_gap_s', 'prep_s', 'warmup_s', 'warmup_ran', 'render_s',
    'frames', 'seconds_per_frame', 'csv_local_used_peak_mb',
)


def _gpu_matches(gpu_memory, gpu):
    """The reported GPU is in the caller's own ``nvidia-smi`` reading (Sec R3-F1)."""
    if not isinstance(gpu_memory, dict) or gpu_memory.get('source') != 'nvidia-smi':
        return False
    for stored in gpu_memory.get('gpus') or []:
        if (isinstance(stored, dict) and stored.get('index') == gpu['index']
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


def job_dependent_errors(job, worker, data):
    """Step 6: the 400 checks that need the job and the caller's stored reading."""
    errors = {}
    if data['engine_build'] != job.engine_build:
        errors['engine_build'] = ["Does not match the job's engine build."]
    if not _gpu_matches(worker.gpu_memory, data['gpu']):
        errors['gpu'] = [GPU_MISMATCH]
    _plausibility_errors(data, errors)
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
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        **results,
        per_shot=[dict(shot) for shot in data['per_shot']],
        worker=worker, worker_hostname=worker.hostname, gpu_index=gpu['index'],
        gpu_name=gpu['name'], gpu_total_mb=gpu['total_mb'], measured_at=now,
    )
    job.status = JobStatus.DONE
    job.completed_at = now
    job.render_time_seconds = round(data['render_s'])
    job.last_output = data['summary']
    job.save(update_fields=['status', 'completed_at', 'render_time_seconds', 'last_output'])


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
        errors = job_dependent_errors(job, worker, data)
        if errors:
            return 400, errors
        now = timezone.now()
        if data['overrides_sha256'] != record.overrides_sha256:
            _fail_pilot(job, now)
            return 409, {'error': CVAR_MISMATCH}
        _store(job, record, worker, data, now)
    peak = data['peak_mb']
    return 200, {'status': JobStatus.DONE, 'peak_mb': peak, 'gate_need_mb': measured_need_mb(peak)}
