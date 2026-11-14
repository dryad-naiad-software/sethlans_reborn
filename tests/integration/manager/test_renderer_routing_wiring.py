# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: worker poll, claim and parent-job checks through the
renderer strategies (spec FR-RT-1..4, FR-MR-4; chunk 1).

Unreal rows are created through the ORM: chunk 1's Unreal strategy
refuses every Unreal request through the API.
"""

from unittest.mock import patch

import pytest
from django.utils.translation import gettext_lazy

from workers.models import Asset, Job, JobStatus, Project, TiledJob
from workers.renderers.blender import BlenderStrategy
from workers.renderers.unreal import NOT_SUPPORTED_MESSAGE, UnrealStrategy

JOBS_URL = '/api/jobs/'
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}
BUILD = '5.8.4-58902052'


@pytest.fixture
def unreal_asset(db):
    project = Project.objects.create(name='RouteUnreal', renderer='UNREAL', engine_build=BUILD)
    return Asset.objects.create(
        name='RouteUnrealAsset', project=project, source_type='shared_project',
        archive_path='incoming/scene.zip', uproject_path='Scene/Scene.uproject',
        archive_sha256='e' * 64,
    )


@pytest.fixture
def mixed_jobs(asset, unreal_asset):
    blender = Job.objects.create(name='RouteBlender', asset=asset, output_file_pattern='//r/b.png')
    unreal = Job.objects.create(name='RouteUnrealJob', asset=unreal_asset, output_file_pattern='//r/u.png')
    return blender, unreal


def _names(resp):
    assert resp.status_code == 200, resp.data
    return sorted(job['name'] for job in resp.data)


@pytest.mark.django_db
class TestPollRenderers:

    @pytest.mark.parametrize('extra', [{}, {'renderers': 'BLENDER'}, {'renderers': ' blender '}])
    def test_blender_only_by_default(self, worker_with_token, mixed_jobs, extra):
        _worker, client = worker_with_token
        assert _names(client.get(JOBS_URL, {**POLL, **extra})) == ['RouteBlender']

    def test_unreal_builds_ignored_without_unreal(self, worker_with_token, mixed_jobs):
        _worker, client = worker_with_token
        resp = client.get(JOBS_URL, {**POLL, 'unreal_builds': 'not-a-build'})
        assert _names(resp) == ['RouteBlender']

    def test_unreal_stub_returns_no_unreal_job(self, worker_with_token, mixed_jobs):
        _worker, client = worker_with_token
        resp = client.get(JOBS_URL, {**POLL, 'renderers': 'UNREAL', 'unreal_builds': BUILD})
        assert _names(resp) == []

    @pytest.mark.parametrize('value, message', [
        ('MAYA', "Invalid renderers value(s): ['MAYA']. Allowed: ['BLENDER', 'UNREAL']."),
        ('BLENDER,maya', "Invalid renderers value(s): ['MAYA']. Allowed: ['BLENDER', 'UNREAL']."),
        (' , ', "renderers must be a non-empty CSV of ['BLENDER', 'UNREAL']."),
    ])
    def test_invalid_renderers_is_400(self, worker_with_token, value, message):
        _worker, client = worker_with_token
        resp = client.get(JOBS_URL, {**POLL, 'renderers': value, 'device_prefs': 'BANANA'})
        assert resp.status_code == 400
        assert resp.data == [message]

    def test_renderers_outside_a_poll_is_ignored(self, admin_client, mixed_jobs):
        assert _names(admin_client.get(JOBS_URL, {'renderers': 'MAYA'})) == ['RouteBlender', 'RouteUnrealJob']

    def test_non_poll_admin_list_returns_both_renderers(self, admin_client, mixed_jobs):
        assert _names(admin_client.get(JOBS_URL)) == ['RouteBlender', 'RouteUnrealJob']

    def test_renderer_filter(self, admin_client, mixed_jobs):
        assert _names(admin_client.get(JOBS_URL, {'renderer': 'UNREAL'})) == ['RouteUnrealJob']
        assert _names(admin_client.get(JOBS_URL, {'renderer': 'BLENDER'})) == ['RouteBlender']


@pytest.mark.django_db
class TestClaimThroughStrategy:

    def _claim(self, client, job, worker):
        return client.post(f'{JOBS_URL}{job.pk}/claim/', {'worker_id': worker.pk}, format='json')

    def test_unreal_claim_rejected_by_stub(self, worker_with_token, mixed_jobs):
        worker, client = worker_with_token
        _blender, unreal = mixed_jobs
        resp = self._claim(client, unreal, worker)
        assert resp.status_code == 409
        assert resp.data == {'error': NOT_SUPPORTED_MESSAGE}
        unreal.refresh_from_db()
        assert (unreal.status, unreal.assigned_worker_id) == (JobStatus.QUEUED, None)

    def test_blender_reclaim_keeps_409(self, worker_with_token, mixed_jobs):
        worker, client = worker_with_token
        blender, _unreal = mixed_jobs
        assert self._claim(client, blender, worker).status_code == 200
        resp = self._claim(client, blender, worker)
        assert resp.status_code == 409
        assert resp.data == {'error': "Job is not available for claiming."}

    def test_idempotent_reclaim_when_the_strategy_allows_it(
        self, worker_with_token, make_worker_client, mixed_jobs,
    ):
        """The view honours ``idempotent_reclaim()`` (chunk 2 turns it on)."""
        worker, client = worker_with_token
        _blender, unreal = mixed_jobs
        Job.objects.filter(pk=unreal.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
        before = Job.objects.values().get(pk=unreal.pk)

        with patch.object(UnrealStrategy, 'idempotent_reclaim', return_value=True):
            resp = self._claim(client, unreal, worker)
            other, other_client, _token = make_worker_client('route-other')
            other_resp = self._claim(other_client, unreal, other)

        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'RENDERING'
        assert Job.objects.values().get(pk=unreal.pk) == before
        assert other_resp.status_code == 409
        assert other_resp.data == {'error': "Job is not available for claiming."}

    @pytest.mark.parametrize('rejection, body', [
        (gettext_lazy("Lazy rejection."), "Lazy rejection."),
        ({'reason': 'dict'}, "{'reason': 'dict'}"),
    ], ids=['lazy-str', 'dict'])
    def test_non_str_rejection_fails_closed(self, worker_with_token, mixed_jobs, rejection, body):
        worker, client = worker_with_token
        blender, _unreal = mixed_jobs
        with patch.object(BlenderStrategy, 'claim_rejection', return_value=rejection):
            resp = self._claim(client, blender, worker)
        assert resp.status_code == 409
        assert resp.data == {'error': body}
        blender.refresh_from_db()
        assert (blender.status, blender.assigned_worker_id, blender.started_at) == (JobStatus.QUEUED, None, None)

    def test_reclaim_flag_off_keeps_409(self, worker_with_token, mixed_jobs):
        worker, client = worker_with_token
        _blender, unreal = mixed_jobs
        Job.objects.filter(pk=unreal.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
        resp = self._claim(client, unreal, worker)
        assert resp.status_code == 409
        assert resp.data == {'error': "Job is not available for claiming."}


@pytest.mark.django_db
class TestParentJobsRejectUnreal:

    def test_tiled_job_create(self, admin_client, unreal_asset):
        resp = admin_client.post('/api/tiled-jobs/', {
            'name': 'RouteTiled', 'project': str(unreal_asset.project_id), 'asset_id': unreal_asset.pk,
            'final_resolution_x': 640, 'final_resolution_y': 480,
            # Not Pillow-compatible: the renderer message still wins.
            'render_settings': {'render.image_settings.file_format': 'OPEN_EXR'},
        }, format='json')
        assert resp.status_code == 400
        assert resp.data == {'non_field_errors': ["Tiled jobs are not supported for Unreal projects yet."]}
        assert not Job.objects.filter(asset=unreal_asset).exists()

    def test_tiled_job_patch_without_asset(self, admin_client, unreal_asset):
        tiled = TiledJob.objects.create(
            name='RouteTiledOrm', project=unreal_asset.project, asset=unreal_asset,
            final_resolution_x=640, final_resolution_y=480,
        )
        resp = admin_client.patch(f'/api/tiled-jobs/{tiled.pk}/', {'name': 'RouteTiled2'}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'non_field_errors': ["Tiled jobs are not supported for Unreal projects yet."]}

    def test_animation_create(self, admin_client, unreal_asset):
        resp = admin_client.post('/api/animations/', {
            'name': 'RouteAnim', 'project': str(unreal_asset.project_id), 'asset_id': unreal_asset.pk,
            'output_file_pattern': '//r/f_####.png', 'start_frame': 1, 'end_frame': 2,
        }, format='json')
        assert resp.status_code == 400
        assert resp.data == {'non_field_errors': ["Animations are not supported for Unreal projects yet."]}

    def test_blender_tiled_job_still_created(self, admin_client, project, asset):
        resp = admin_client.post('/api/tiled-jobs/', {
            'name': 'RouteTiledB', 'project': str(project.pk), 'asset_id': asset.pk,
            'final_resolution_x': 640, 'final_resolution_y': 480,
            'tile_count_x': 2, 'tile_count_y': 2,
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert set(Job.objects.filter(tiled_job_id=resp.data['id']).values_list('renderer', flat=True)) == {'BLENDER'}
