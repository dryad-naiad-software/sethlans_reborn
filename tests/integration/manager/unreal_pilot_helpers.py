# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the chunk 2b measured-peak tests (Unreal foundation
FR-PM, FR-T-3 ``test_unreal_pilot*.py``).

Not a test module: imported by the ``test_unreal_pilot*.py`` and
``test_unreal_measurement*.py`` files.
"""

from django.utils import timezone

from shared.unreal.validation import overrides_sha256

from workers.models import Job, UnrealMeasurement

from .unreal_helpers import BUILD, RENDER_SETTINGS, SHARE, caps, gpu_memory, set_share

JOBS = '/api/jobs/'
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true',
        'renderers': 'BLENDER,UNREAL', 'unreal_builds': BUILD}
OTHER_SEQUENCE = '/Game/Cinematics/Other.Other'


def settings(**overrides):
    """``RENDER_SETTINGS`` with some keys replaced (``sequence=...``)."""
    result = dict(RENDER_SETTINGS)
    for key, value in overrides.items():
        result[f'unreal.{key}'] = value
    return result


def create_job(client, asset, name='Still01', frame=1000, render_settings=None, **extra):
    """POST an Unreal job; return the response."""
    body = {
        'name': name, 'asset_id': asset.pk, 'output_file_pattern': '//render/still.png',
        'start_frame': frame, 'end_frame': frame,
        'render_settings': dict(render_settings or RENDER_SETTINGS),
    }
    body.update(extra)
    return client.post(JOBS, body, format='json')


def created_job(client, asset, name='Still01', **kwargs):
    resp = create_job(client, asset, name=name, **kwargs)
    assert resp.status_code == 201, resp.data
    return Job.objects.get(pk=resp.data['id'])


def pilots(asset=None):
    queryset = Job.objects.filter(unreal_kind='pilot').order_by('id')
    return list(queryset.filter(asset=asset) if asset is not None else queryset)


def record_of(pilot):
    return UnrealMeasurement.objects.get(pilot_job=pilot)


def ready_worker(worker, total_mb=32607, source='nvidia-smi'):
    """Make ``worker`` eligible for Unreal jobs on the manager's share."""
    set_share(SHARE)
    worker.unreal_capabilities = caps()
    memory = gpu_memory(total_mb)
    memory['source'] = source
    worker.gpu_memory = memory
    worker.save(update_fields=['unreal_capabilities', 'gpu_memory'])
    return worker


def claim(client, job, worker):
    return client.post(f'{JOBS}{job.pk}/claim/', {'worker_id': worker.pk}, format='json')


def measure(record, peak_mb=8102, worker=None):
    """Store a successful result on ``record`` (as ``pilot-result/`` would)."""
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        peak_mb=peak_mb, peak_source='csv', measured_at=timezone.now(),
        worker=worker, worker_hostname=getattr(worker, 'hostname', ''),
    )
    if record.pilot_job_id is not None:
        Job.objects.filter(pk=record.pilot_job_id).update(status='DONE')


def measure_key(job, peak_mb=8102):
    """Store a successful measurement for ``job``'s key (no pilot job).

    For tests written before chunk 2b, whose ORM-created jobs would
    otherwise be held as unmeasured (state "none", AC-46).
    """
    job.refresh_from_db()
    project = job.asset.project
    settings_ = job.render_settings if isinstance(job.render_settings, dict) else {}
    return UnrealMeasurement.objects.create(
        project=project, asset=job.asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=job.unreal_target_sha256, engine_build=job.engine_build,
        map=settings_.get('unreal.map', ''), sequence=settings_.get('unreal.sequence', ''),
        preset=settings_.get('unreal.preset', ''), peak_mb=peak_mb, peak_source='card',
        measured_at=timezone.now(),
    )


def pilot_body(**overrides):
    """A ``pilot-result/`` body that passes every rule for ``ready_worker``."""
    body = {
        'peak_mb': 8102, 'peak_source': 'csv', 'card_delta_peak_mb': 7930,
        'baseline_used_mb': 945, 'baseline_drift_mb': 12, 'peak_phase': 'render',
        'max_sample_gap_s': 1.2, 'prep_s': 24.1, 'warmup_s': 800.2, 'warmup_ran': True,
        'render_s': 1380.5, 'frames': 23, 'seconds_per_frame': 60.0,
        'per_shot': [{'frame': 0, 'render_s': 58.2}],
        'gpu': {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607},
        'engine_build': BUILD, 'csv_local_used_peak_mb': 8102,
        'overrides_sha256': overrides_sha256({}), 'summary': 'pilot_frames=23',
    }
    body.update(overrides)
    return body


def post_result(client, job_pk, body):
    return client.post(f'{JOBS}{job_pk}/pilot-result/', body, format='json')


def claimed_pilot(admin_client, asset, worker, worker_client, name='Still01', **kwargs):
    """Create a job (and its pilot) and claim the pilot as ``worker``."""
    created_job(admin_client, asset, name=name, **kwargs)
    pilot = pilots(asset)[-1]
    ready_worker(worker)
    resp = claim(worker_client, pilot, worker)
    assert resp.status_code == 200, resp.data
    pilot.refresh_from_db()
    return pilot
