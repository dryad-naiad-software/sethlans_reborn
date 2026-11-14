# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: where the measurement fields appear and what they cost
(Unreal foundation FR-PM-5, FR-PM-7, FR-PM-8, AC-51, AC-55).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from shared.unreal.vram_gate import measured_need_mb

from workers.models import Job

from .unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset, unreal_project
from .unreal_pilot_helpers import JOBS, OTHER_SEQUENCE, created_job, measure, pilots, record_of, settings

PROJECTS = '/api/projects/'


def _measurement(client, project_id):
    resp = client.get(f'{PROJECTS}{project_id}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_measurement']


def _count(client, url, params=None):
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url, params or {})
    assert resp.status_code == 200, resp.data
    return len(ctx.captured_queries), resp


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.mark.django_db
class TestProjectField:

    def test_no_record(self, admin_client, asset):
        Job.objects.create(name='Legacy', asset=asset, output_file_pattern='//r/f.png',
                           render_settings=dict(RENDER_SETTINGS))
        assert _measurement(admin_client, asset.project_id) == {
            'held_pending': 0, 'held_failed': 0, 'held_none': 1, 'latest': None}

    def test_pending_and_counts(self, admin_client, asset):
        created_job(admin_client, asset, name='JobA1')
        created_job(admin_client, asset, name='JobA2')
        created_job(admin_client, asset, name='JobB1', render_settings=settings(sequence=OTHER_SEQUENCE))
        Job.objects.filter(pk=pilots()[-1].pk).update(status='ERROR', error_message='boom')
        data = _measurement(admin_client, asset.project_id)
        assert (data['held_pending'], data['held_failed'], data['held_none']) == (2, 1, 0)
        latest = data['latest']
        assert (latest['status'], latest['reason'], latest['pilot_job_id']) == ('failed', 'boom', pilots()[-1].pk)
        assert latest['sequence'] == OTHER_SEQUENCE
        assert latest['peak_mb'] is None and latest['gate_need_mb'] is None and latest['measured_at'] is None

    def test_measured(self, admin_client, asset, worker_with_token):
        worker, _client = worker_with_token
        created_job(admin_client, asset)
        record = record_of(pilots()[0])
        measure(record, peak_mb=8102, worker=worker)
        data = _measurement(admin_client, asset.project_id)
        assert (data['held_pending'], data['held_failed'], data['held_none']) == (0, 0, 0)
        latest = data['latest']
        assert latest['measurement_id'] == record.pk
        assert (latest['status'], latest['reason'], latest['remeasure']) == ('measured', None, False)
        assert (latest['peak_mb'], latest['gate_need_mb']) == (8102, measured_need_mb(8102))
        assert (latest['asset_id'], latest['engine_build'], latest['worker_hostname']) == (
            asset.pk, BUILD, worker.hostname)
        assert latest['measured_at'].endswith('Z')

    def test_latest_follows_the_current_cvars(self, admin_client, asset):
        created_job(admin_client, asset)
        admin_client.patch(f'{PROJECTS}{asset.project_id}/',
                           {'unreal_cvar_overrides': {'r.Streaming.PoolSize': '3000'}}, format='json')
        latest = _measurement(admin_client, asset.project_id)['latest']
        assert latest['pilot_job_id'] == pilots()[-1].pk

    def test_list_and_update_responses_carry_it(self, admin_client, asset):
        created_job(admin_client, asset)
        listed = admin_client.get(PROJECTS).data
        assert listed[0]['unreal_measurement']['held_pending'] == 1
        resp = admin_client.patch(f'{PROJECTS}{asset.project_id}/', {'name': 'Renamed'}, format='json')
        assert resp.data['unreal_measurement']['held_pending'] == 1
        resp = admin_client.post(PROJECTS, {'name': 'NewUnreal', 'renderer': 'UNREAL', 'engine_build': BUILD},
                                 format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['unreal_measurement'] == {
            'held_pending': 0, 'held_failed': 0, 'held_none': 0, 'latest': None}

    def test_blender_project_is_null(self, admin_client, project):
        assert _measurement(admin_client, project.pk) is None

    def test_not_nested(self, admin_client, asset):
        job = created_job(admin_client, asset)
        nested = admin_client.get(f'{JOBS}{job.pk}/').data['asset']['project_details']
        assert 'unreal_measurement' not in nested
        assert 'unreal_measurement' not in admin_client.get('/api/assets/').data[0]['project_details']


@pytest.mark.django_db
class TestQueryCounts:

    def test_project_list_adds_no_query_per_project(self, admin_client):
        for i in range(2):
            created_job(admin_client, unreal_asset(unreal_project(name=f'Proj{i}'), name=f'Asset{i}'))
        few, _ = _count(admin_client, PROJECTS)
        for i in range(2, 6):
            created_job(admin_client, unreal_asset(unreal_project(name=f'Proj{i}'), name=f'Asset{i}'))
        many, resp = _count(admin_client, PROJECTS)
        assert len(resp.data) == 6 and many == few

    def test_job_list_adds_no_query_for_the_peak(self, admin_client, asset):
        created_job(admin_client, asset, name='Job0')
        measure(record_of(pilots()[0]))
        few, _ = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        for i in range(1, 8):
            created_job(admin_client, asset, name=f'Job{i}')
        many, resp = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        assert many == few
        assert {row['unreal_measured_peak_mb'] for row in resp.data if row['unreal_kind'] == 'render'} == {8102}

    def test_create_response_uses_the_fallback(self, admin_client, asset):
        created_job(admin_client, asset, name='Job0')
        measure(record_of(pilots()[0]), peak_mb=9000)
        resp = admin_client.post(JOBS, {
            'name': 'Job1', 'asset_id': asset.pk, 'output_file_pattern': '//r/f.png',
            'start_frame': 1, 'end_frame': 1, 'render_settings': dict(RENDER_SETTINGS)}, format='json')
        assert resp.status_code == 201, resp.data
        assert (resp.data['unreal_kind'], resp.data['unreal_measured_peak_mb']) == ('render', 9000)

    def test_blender_lists_are_unchanged(self, admin_client, project):
        """AC-51: the counts measured on master before chunk 2b (b83dec2) were 1 query each."""
        from workers.models import Asset
        blend = Asset.objects.create(name='BlendQ', project=project, blend_file=b'BLENDER-v300')
        job = Job.objects.create(name='BQ1', asset=blend, output_file_pattern='//r/b.png')
        assert _count(admin_client, JOBS)[0] == 1
        assert _count(admin_client, f'{JOBS}{job.pk}/')[0] == 1
        assert _count(admin_client, PROJECTS)[0] == 1
        assert _count(admin_client, f'{PROJECTS}{project.pk}/')[0] == 1
        Job.objects.create(name='BQ2', asset=blend, output_file_pattern='//r/b.png')
        count, resp = _count(admin_client, JOBS)
        assert count == 1
        assert all(row['unreal_kind'] == 'render' and row['unreal_measured_peak_mb'] is None for row in resp.data)
        assert all(row['unreal_measurement'] is None for row in _count(admin_client, PROJECTS)[1].data)
