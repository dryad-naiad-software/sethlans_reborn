# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: Unreal jobs through the API (Unreal foundation FR-MU-3,
FR-MU-10..12, FR-MR-4, FR-MR-11, FR-T-3, AC-26).
"""

import pytest
from django.core.files.base import ContentFile

from workers.models import Asset, Job, JobStatus, Project

from .unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset

URL = '/api/jobs/'


@pytest.fixture
def asset(db):
    return unreal_asset()


def _body(asset, **extra):
    body = {
        'name': 'UnrealStill', 'asset_id': asset.pk, 'output_file_pattern': '//render/still.png',
        'start_frame': 1000, 'end_frame': 1000, 'render_settings': dict(RENDER_SETTINGS),
    }
    body.update(extra)
    return body


def _post(client, asset, **extra):
    return client.post(URL, _body(asset, **extra), format='json')


def _job(asset, **extra):
    fields = {'name': 'OrmStill', 'asset': asset, 'output_file_pattern': '//r/f.png',
              'start_frame': 1000, 'end_frame': 1000, 'render_device': 'GPU',
              'render_settings': {**RENDER_SETTINGS, 'unreal.output_format': 'PNG'}}
    fields.update(extra)
    return Job.objects.create(**fields)


@pytest.mark.django_db
class TestCreate:

    def test_still_is_created_with_defaults(self, admin_client, asset):
        resp = _post(admin_client, asset)
        assert resp.status_code == 201, resp.data
        job = Job.objects.get(pk=resp.data['id'])
        assert (job.renderer, job.engine_build, job.render_device) == ('UNREAL', BUILD, 'GPU')
        assert job.render_settings['unreal.output_format'] == 'PNG'
        assert resp.data['effective_blender_version'] is None

    def test_explicit_build_is_pinned(self, admin_client, asset):
        resp = _post(admin_client, asset, engine_build='5.8.3-1')
        assert resp.status_code == 201, resp.data
        asset.project.engine_build = '5.8.4-99'
        asset.project.save()
        assert Job.objects.get(pk=resp.data['id']).engine_build == '5.8.3-1'

    def test_project_build_edit_does_not_change_existing_jobs(self, admin_client, asset):
        job_id = _post(admin_client, asset).data['id']
        asset.project.engine_build = '5.8.5-1'
        asset.project.save()
        assert Job.objects.get(pk=job_id).engine_build == BUILD

    @pytest.mark.parametrize('settings, message_part', [
        ({**RENDER_SETTINGS, 'cycles.samples': 5}, 'Unknown Unreal render settings: cycles.samples.'),
        ({'unreal.map': '/Game/M.M', 'unreal.sequence': '/Game/S.S'}, "'unreal.preset' is required."),
        ({**RENDER_SETTINGS, 'unreal.map': 'Game/M'}, "'unreal.map':"),
        ({**RENDER_SETTINGS, 'unreal.output_format': 'JPEG'}, "Unreal jobs support PNG output only"),
        ({**RENDER_SETTINGS, 'unreal.output_format': 'OPEN_EXR'}, "Unreal jobs support PNG output only"),
    ])
    def test_strict_settings(self, admin_client, asset, settings, message_part):
        resp = _post(admin_client, asset, render_settings=settings)
        assert resp.status_code == 400
        assert any(message_part in str(m) for m in resp.data['render_settings'])

    @pytest.mark.parametrize('extra, field', [
        ({'start_frame': 1, 'end_frame': 2}, 'end_frame'),
        ({'start_frame': 10_000_001, 'end_frame': 10_000_001}, 'start_frame'),
        ({'render_device': 'CPU'}, 'render_device'),
        ({'render_device': 'ANY'}, 'render_device'),
        ({'render_engine': 'BLENDER_EEVEE_NEXT'}, 'render_engine'),
        ({'cycles_feature_set': 'EXPERIMENTAL'}, 'cycles_feature_set'),
        ({'engine_build': '5.7.4-51494982'}, 'engine_build'),
        ({'output_file_pattern': '//render/still.jpg'}, 'output_file_pattern'),
    ])
    def test_still_rules(self, admin_client, asset, extra, field):
        resp = _post(admin_client, asset, **extra)
        assert resp.status_code == 400
        assert field in resp.data

    def test_blender_version_not_applicable(self, admin_client, asset, default_version):
        resp = _post(admin_client, asset, blender_version=default_version.pk)
        assert resp.status_code == 400
        assert resp.data['blender_version'] == ["Not applicable to Unreal jobs."]

    def test_negative_frame_and_explicit_gpu(self, admin_client, asset):
        resp = _post(admin_client, asset, start_frame=-5, end_frame=-5, render_device='GPU',
                     render_engine='CYCLES')
        assert resp.status_code == 201, resp.data


@pytest.mark.django_db
class TestPatch:

    def test_start_frame_only_is_checked_against_end_frame(self, admin_client, asset):
        job = _job(asset)
        resp = admin_client.patch(f'{URL}{job.pk}/', {'start_frame': 5}, format='json')
        assert resp.status_code == 400
        assert 'end_frame' in resp.data

    def test_render_settings_change_rechecks_the_pattern(self, admin_client, asset):
        job = _job(asset, output_file_pattern='//r/f.jpg')
        resp = admin_client.patch(f'{URL}{job.pk}/', {'render_settings': RENDER_SETTINGS}, format='json')
        assert resp.status_code == 400
        assert 'output_file_pattern' in resp.data

    def test_engine_build_change(self, admin_client, asset):
        job = _job(asset)
        resp = admin_client.patch(f'{URL}{job.pk}/', {'engine_build': '5.8.4-1'}, format='json')
        assert resp.data == {'engine_build': ["Engine build cannot be changed after creation."]}

    def test_blender_job_to_shared_asset(self, admin_client, asset, default_version):
        project = Project.objects.create(name='BlenderP', blender_version=default_version)
        blend = Asset.objects.create(name='BlendA', project=project,
                                     blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='a.blend'))
        job = Job.objects.create(name='BlenderJob', asset=blend, output_file_pattern='//r/f.png')
        resp = admin_client.patch(f'{URL}{job.pk}/', {'asset_id': asset.pk}, format='json')
        assert resp.data == {'asset_id': ["Cannot move a job to an asset of a different renderer."]}

    def test_rename_is_allowed(self, admin_client, asset):
        job = _job(asset)
        resp = admin_client.patch(f'{URL}{job.pk}/', {'name': 'Renamed'}, format='json')
        assert resp.status_code == 200, resp.data

    def test_worker_status_patch_is_tolerated(self, worker_with_token, asset):
        worker, client = worker_with_token
        job = _job(asset)
        Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
        resp = client.patch(f'{URL}{job.pk}/', {
            'status': 'DONE', 'last_output': 'ok', 'render_time_seconds': 12,
            'completed_at': '2026-11-14T00:00:00Z', 'error_message': '',
        }, format='json')
        assert resp.status_code == 200, resp.data
        assert Job.objects.get(pk=job.pk).status == JobStatus.DONE


@pytest.mark.django_db
class TestParentJobs:

    def test_tiled_job_and_animation_are_refused(self, admin_client, asset):
        common = {'name': 'Parent', 'project': str(asset.project_id), 'asset_id': asset.pk}
        tiled = admin_client.post('/api/tiled-jobs/', {
            **common, 'final_resolution_x': 100, 'final_resolution_y': 100,
            'tile_count_x': 2, 'tile_count_y': 2,
        }, format='json')
        assert tiled.data == {'non_field_errors': ["Tiled jobs are not supported for Unreal projects yet."]}
        anim = admin_client.post('/api/animations/', {
            **common, 'start_frame': 1, 'end_frame': 2, 'output_file_pattern': '//r/f_####.png',
        }, format='json')
        assert anim.data == {'non_field_errors': ["Animations are not supported for Unreal projects yet."]}
