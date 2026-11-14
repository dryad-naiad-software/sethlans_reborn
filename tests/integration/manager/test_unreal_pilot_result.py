# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``POST /api/jobs/{id}/pilot-result/`` (Unreal foundation
FR-PM-4, AC-47): success, the strict body, the check order, the GPU
cross-check, plausibility and the CVar-mismatch 409.
"""

import json

import pytest

from shared.unreal.validation import overrides_sha256

from workers.models import Job

from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    JOBS, claimed_pilot, created_job, pilot_body, pilots, post_result, ready_worker, record_of,
)


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


def _raw(client, pk, text):
    return client.post(f'{JOBS}{pk}/pilot-result/', data=text, content_type='application/json')


@pytest.mark.django_db
class TestSuccess:

    def test_stores_the_result_and_finishes_the_pilot(self, pilot):
        job, worker, client = pilot
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102, 'gate_need_mb': 9318})
        job.refresh_from_db()
        assert (job.status, job.render_time_seconds, job.last_output) == ('DONE', 1380, 'pilot_frames=23')
        assert job.completed_at is not None
        record = record_of(job)
        assert (record.peak_mb, record.peak_source, record.card_delta_peak_mb) == (8102, 'csv', 7930)
        assert (record.baseline_used_mb, record.baseline_drift_mb, record.peak_phase) == (945, 12, 'render')
        assert (record.max_sample_gap_s, record.prep_s, record.warmup_s, record.warmup_ran) == (1.2, 24.1, 800.2, True)
        assert (record.render_s, record.frames, record.seconds_per_frame) == (1380.5, 23, 60.0)
        assert record.per_shot == [{'frame': 0, 'render_s': 58.2}]
        assert (record.worker_id, record.worker_hostname, record.gpu_index) == (worker.pk, worker.hostname, 0)
        assert (record.gpu_name, record.gpu_total_mb, record.csv_local_used_peak_mb) == (
            'NVIDIA GeForce RTX 5090', 32607, 8102)
        assert record.measured_at is not None

    def test_card_peak_and_null_optionals(self, pilot):
        job, _worker, client = pilot
        body = pilot_body(peak_mb=7930, peak_source='card', csv_local_used_peak_mb=None, warmup_s=None)
        assert post_result(client, job.pk, body).status_code == 200

    def test_repeat_gets_409(self, pilot):
        job, _worker, client = pilot
        assert post_result(client, job.pk, pilot_body()).status_code == 200
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (409, {'error': 'Job is not rendering on this worker.'})

    def test_measured_job_is_then_offered(self, admin_client, pilot):
        job, worker, client = pilot
        post_result(client, job.pk, pilot_body())
        normal = Job.objects.get(name='Still01')
        assert admin_client.get(f'{JOBS}{normal.pk}/').data['unreal_measured_peak_mb'] == 8102


@pytest.mark.django_db
class TestCheckOrder:

    def test_malformed_body_for_a_missing_job_is_400(self, worker_with_token):
        _worker, client = worker_with_token
        resp = post_result(client, 999999, {'peak_mb': 'x'})
        assert resp.status_code == 400

    @pytest.mark.parametrize('pk', ['999999', 'abc', '99999999999999999999999'])
    def test_valid_body_for_a_missing_job_is_404(self, worker_with_token, pk):
        _worker, client = worker_with_token
        resp = post_result(client, pk, pilot_body())
        assert (resp.status_code, resp.data) == (404, {'error': 'Job not found.'})

    def test_non_owner_before_kind(self, admin_client, asset, worker_with_token, make_worker_client):
        created_job(admin_client, asset)
        normal = Job.objects.get(name='Still01')
        _other, other_client, _token = make_worker_client('other-host')
        for job in (normal, pilots()[0]):
            resp = post_result(other_client, job.pk, pilot_body())
            assert resp.data == {'error': 'Job is not rendering on this worker.'}

    def test_normal_job_is_not_a_pilot(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        created_job(admin_client, asset)
        normal = Job.objects.get(name='Still01')
        Job.objects.filter(pk=normal.pk).update(status='RENDERING', assigned_worker=worker)
        resp = post_result(client, normal.pk, pilot_body())
        assert (resp.status_code, resp.data) == (409, {'error': 'Job is not a pilot job.'})

    def test_blender_job_is_kind_409(self, asset_factory_blender, worker_with_token):
        worker, client = worker_with_token
        job = asset_factory_blender(worker)
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (409, {'error': 'Pilot results are not supported for this renderer.'})


@pytest.fixture
def asset_factory_blender(asset):
    from workers.models import Asset, Project
    from workers.models import SupportedBlenderVersion

    def _make(worker):
        version = SupportedBlenderVersion.objects.create(series='4.2', resolved_version='4.2.1', is_default=True)
        project = Project.objects.create(name='BlendProj', blender_version=version)
        blend = Asset.objects.create(name='BlendAsset', project=project, blend_file=b'BLENDER-v300')
        return Job.objects.create(name='BlendJob', asset=blend, output_file_pattern='//r/b.png',
                                  status='RENDERING', assigned_worker=worker)
    return _make


def _unknown(resp):
    return resp.status_code, resp.data


BOUND_CASES = [
    ('peak_mb', True), ('peak_mb', '8102'), ('peak_mb', 8102.0), ('peak_mb', 1023),
    ('peak_mb', 40000), ('frames', 0), ('frames', 257), ('prep_s', '24'), ('prep_s', -1),
    ('prep_s', 259201), ('max_sample_gap_s', 10.5), ('warmup_ran', 1), ('warmup_ran', 'true'),
    ('peak_source', 'pdh'), ('peak_phase', 'idle'), ('engine_build', '5.8.4'),
    ('engine_build', 58), ('overrides_sha256', 'AB' * 32), ('overrides_sha256', 'ab' * 31),
    ('card_delta_peak_mb', -1), ('card_delta_peak_mb', 40000), ('baseline_used_mb', 40000),
    ('baseline_drift_mb', -5), ('csv_local_used_peak_mb', 40000), ('summary', 'x' * 32769),
    ('summary', 5), ('per_shot', [{'frame': -1, 'render_s': 1}]),
    ('per_shot', [{'frame': 2 ** 31, 'render_s': 1}]), ('per_shot', [{'frame': 0, 'render_s': 1}] * 257),
    ('gpu', {'index': 64, 'name': 'x', 'total_mb': 32607}), ('gpu', {'index': 0, 'name': 'x', 'total_mb': 0}),
    ('prep_s', None), ('render_s', None),
]


@pytest.mark.django_db
class TestStrictBody:

    @pytest.mark.parametrize('body, keys', [
        (pilot_body(extra=1), 'extra'),
        (pilot_body(gpu={'index': 0, 'name': 'x', 'total_mb': 32607, 'vendor': 'nv'}), 'gpu.vendor'),
        (pilot_body(per_shot=[{'frame': 0, 'render_s': 1}, {'frame': 1, 'render_s': 1, 'x': 2}]), 'per_shot[1].x'),
        (pilot_body(zz=1, aa=2), 'aa, zz'),
    ])
    def test_unknown_keys(self, pilot, body, keys):
        job, _worker, client = pilot
        assert _unknown(post_result(client, job.pk, body)) == (400, {'detail': f'Unknown keys: {keys}.'})

    @pytest.mark.parametrize('field, value', BOUND_CASES, ids=[f'{f}-{i}' for i, (f, _v) in enumerate(BOUND_CASES)])
    def test_bound_rejections_change_nothing(self, pilot, field, value):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, pilot_body(**{field: value}))
        assert resp.status_code == 400, resp.data
        job.refresh_from_db()
        assert job.status == 'RENDERING'
        assert record_of(job).peak_mb is None

    @pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity'])
    def test_non_finite_numbers_are_rejected(self, pilot, token):
        job, _worker, client = pilot
        text = json.dumps(pilot_body()).replace('"prep_s": 24.1', f'"prep_s": {token}')
        assert _raw(client, job.pk, text).status_code == 400

    def test_missing_field(self, pilot):
        job, _worker, client = pilot
        body = pilot_body()
        del body['gpu']
        assert post_result(client, job.pk, body).status_code == 400

    def test_gpu_name_is_sanitized_not_refused(self, pilot):
        job, _worker, client = pilot
        body = pilot_body(gpu={'index': 0, 'name': '<script>', 'total_mb': 32607})
        assert post_result(client, job.pk, body).status_code == 200
        assert record_of(job).gpu_name == ''


@pytest.mark.django_db
class TestJobDependent:

    def test_engine_build_mismatch(self, pilot):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, pilot_body(engine_build='5.8.4-1'))
        assert resp.status_code == 400 and 'engine_build' in resp.data

    @pytest.mark.parametrize('memory', [
        {}, {'source': 'override'}, {'other_index': True}, {'total_mb': 32607 - 65},
    ])
    def test_gpu_cross_check(self, pilot, memory):
        job, worker, client = pilot
        stored = dict(worker.gpu_memory)
        gpu = dict(stored['gpus'][0])
        if memory == {}:
            stored = {}
        elif 'source' in memory:
            stored['source'] = 'override'
        elif 'other_index' in memory:
            gpu['index'] = 1
        else:
            gpu['total_mb'] = memory['total_mb']
        if stored:
            stored['gpus'] = [gpu]
        worker.gpu_memory = stored
        worker.save(update_fields=['gpu_memory'])
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (400, {'gpu': ["Does not match this worker's reported GPU memory."]})

    def test_total_within_64_is_accepted(self, pilot):
        job, worker, client = pilot
        worker.gpu_memory['gpus'][0]['total_mb'] = 32607 - 64
        worker.save(update_fields=['gpu_memory'])
        assert post_result(client, job.pk, pilot_body()).status_code == 200

    @pytest.mark.parametrize('overrides, field', [
        ({'peak_mb': 8000}, 'peak_mb'),
        ({'peak_source': 'card'}, 'peak_source'),
        ({'peak_mb': 32000, 'card_delta_peak_mb': 32000, 'csv_local_used_peak_mb': None,
          'peak_source': 'card', 'baseline_used_mb': 700}, 'peak_mb'),
    ])
    def test_plausibility(self, pilot, overrides, field):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, pilot_body(**overrides))
        assert resp.status_code == 400 and field in resp.data, resp.data
        assert record_of(job).peak_mb is None


@pytest.mark.django_db
def test_cvar_mismatch_fails_the_pilot(pilot):
    job, _worker, client = pilot
    resp = post_result(client, job.pk, pilot_body(overrides_sha256=overrides_sha256({'a.b': '1'})))
    assert (resp.status_code, resp.data) == (409, {'error': 'Pilot rendered different CVars than its measurement key.'})
    job.refresh_from_db()
    assert (job.status, job.error_message) == (
        'ERROR', 'Pilot render failed: it rendered different CVars than its measurement key.')
    assert record_of(job).peak_mb is None
