# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the chunk 2b measured-peak tests (Unreal foundation
FR-PM, FR-T-3 ``test_unreal_pilot*.py``).

Not a test module: imported by the ``test_unreal_pilot*.py`` and
``test_unreal_measurement*.py`` files.
"""

import copy

from django.utils import timezone

from shared.unreal.validation import overrides_sha256
from tests.unit.manager.card_report_helpers import card, stored

from workers.models import Job, UnrealMeasurement, Worker

from .unreal_helpers import BUILD, RENDER_SETTINGS, SHARE, caps, card_key_of, gpu_memory, set_share

JOBS = '/api/jobs/'
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true',
        'renderers': 'BLENDER,UNREAL', 'unreal_builds': BUILD}
OTHER_SEQUENCE = '/Game/Cinematics/Other.Other'
# The card kind ``ready_worker`` gives (per-machine memory test D1): card_report_helpers.card().
KIND = '10de:2b85:00a1'
# A driver version as UE logs it (``internal:`` value of the Driver Version line).
DRIVER = '32.0.16.1692'


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
    """Make ``worker`` eligible for Unreal jobs on the manager's share.

    One ticked card of kind ``KIND`` (per-machine D5); with ``source='override'``
    the worker has no live card reading and so no kind (D14).
    """
    set_share(SHARE)
    worker.unreal_capabilities = caps()
    worker.save(update_fields=['unreal_capabilities'])
    return live_card(worker, total_mb=total_mb, source=source)


def live_card(worker, total_mb=32607, source='nvidia-smi'):
    """Give ``worker`` a stored card reading and one ticked card of kind ``KIND`` (capabilities untouched)."""
    worker.gpu_memory = gpu_memory(total_mb, source=source)
    worker.card_status = stored(cards=[card(key=card_key_of(0))], alerts=[], notices=[], warnings=[])
    worker.save(update_fields=['gpu_memory', 'card_status', 'last_seen'])
    return worker


def claim(client, job, worker):
    return client.post(f'{JOBS}{job.pk}/claim/', {'worker_id': worker.pk}, format='json')


def measure(record, peak_mb=8102, worker=None, game_overrides='preset', texture_streaming='fully_load',
            verdict='fits'):
    """Store a successful result on ``record`` (as ``pilot-result/`` would); its pair keeps its worker."""
    who = {} if worker is None else {'worker': worker, 'worker_hostname': worker.hostname}
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        peak_mb=peak_mb, peak_source='csv', measured_at=timezone.now(), verdict=verdict, verdict_source='pilot',
        game_overrides=game_overrides, texture_streaming=texture_streaming, **who,
    )
    if record.pilot_job_id is not None:
        Job.objects.filter(pk=record.pilot_job_id).update(status='DONE')


def fits_record(asset, target_sha256, worker, peak_mb=8102, kind=KIND, ram=None, **fields):
    """A current ``fits`` result of ``worker`` and ``kind`` for a key (no pilot job, per-machine D2)."""
    project = asset.project
    UnrealMeasurement.objects.filter(
        asset=asset, overrides_sha256=project.unreal_overrides_sha256, target_sha256=target_sha256,
        worker=worker, card_kind=kind, current=True,
    ).update(current=False)
    values = dict(engine_build=BUILD, map=RENDER_SETTINGS['unreal.map'], sequence=RENDER_SETTINGS['unreal.sequence'],
                  preset=RENDER_SETTINGS['unreal.preset'], peak_mb=peak_mb, peak_source='card', ram=ram,
                  measured_at=timezone.now(), worker=worker, worker_hostname=worker.hostname, card_kind=kind,
                  current=True, verdict='fits', verdict_source='pilot')
    values.update(fields)
    return UnrealMeasurement.objects.create(
        project=project, asset=asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=target_sha256, **values,
    )


def measure_key(job, peak_mb=8102, worker=None, ram=None):
    """Store a ``fits`` result of ``job``'s key (no pilot job) for ``worker``, else for every worker.

    For tests written before chunk 2b, whose ORM-created jobs would
    otherwise be held as unmeasured (AC-46).  Per machine (D2) a result
    belongs to one worker and card kind: ``KIND``, the kind ``ready_worker``
    gives.  Returns the last record.
    """
    job.refresh_from_db()
    workers = [worker] if worker is not None else list(Worker.objects.order_by('pk'))
    record = None
    for each in workers:
        record = fits_record(job.asset, job.unreal_target_sha256, each, peak_mb=peak_mb, ram=ram)
    return record


# A pilot's RAM figures (per-card spec FR-PC-RAM-3): commit ~ working set + GPU memory (D8).
PILOT_RAM = {
    'prep': {'ws_mb': 5200, 'commit_mb': 9100},
    'warmup': {'ws_mb': 9400, 'commit_mb': 24600, 'cold': True},
    'render': {'ws_mb': 7100, 'commit_mb': 20700},
}
PILOT_RAM_PROCESS_COMMIT_PEAK_MB = 18900


def pilot_body(**overrides):
    """A ``pilot-result/`` body that passes every rule for ``ready_worker``."""
    body = {
        'ram': copy.deepcopy(PILOT_RAM), 'ram_process_commit_peak_mb': PILOT_RAM_PROCESS_COMMIT_PEAK_MB,
        'shared_machine': False,
        'peak_mb': 8102, 'peak_source': 'csv', 'card_delta_peak_mb': 7930,
        'baseline_used_mb': 945, 'baseline_drift_mb': 12, 'peak_phase': 'render',
        'max_sample_gap_s': 1.2, 'prep_s': 24.1, 'warmup_s': 800.2, 'warmup_ran': True,
        'render_s': 1380.5, 'frames': 23, 'seconds_per_frame': 60.0,
        'per_shot': [{'frame': 0, 'render_s': 58.2}],
        'gpu': {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607, 'card_key': card_key_of(0)},
        'engine_build': BUILD, 'csv_local_used_peak_mb': 8102,
        'overrides_sha256': overrides_sha256({}), 'summary': 'pilot_frames=23',
        'game_overrides': 'implicit', 'texture_streaming': 'off',
        # Per-machine memory test FR-MT-V-3: the pilot's card kind, driver and D6 verdict.
        'card_kind': KIND, 'driver_version': DRIVER, 'verdict': 'fits', 'shared_peak_mb': None,
        'lower_bound_mb': None,
    }
    body.update(overrides)
    return body


def post_result(client, job_pk, body):
    return client.post(f'{JOBS}{job_pk}/pilot-result/', body, format='json')


def claimed_pilot(admin_client, asset, worker, worker_client, name='Still01', **kwargs):
    """Create a job (and ``worker``'s pilot) and claim the pilot as ``worker``."""
    ready_worker(worker)
    created_job(admin_client, asset, name=name, **kwargs)
    pilot = pilots(asset)[-1]
    resp = claim(worker_client, pilot, worker)
    assert resp.status_code == 200, resp.data
    pilot.refresh_from_db()
    return pilot
