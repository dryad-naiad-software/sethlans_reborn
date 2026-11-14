# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: renderer fields and checks on the Project, Asset and
Job serializers (spec FR-MR-10/11, FR-MU-8/13/14; chunk 1).

* the additive response fields and their Blender defaults;
* the generic immutability checks (``renderer_checks.py``), which run
  before strategy dispatch;
* the Blender strategy's rejection of Unreal-only keys;
* Unreal creates reach the Unreal strategy (a 201 or a 400, never a 500);
* ``blend_file`` required-ness through ``AssetSerializer.get_fields()``.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from workers.models import Asset, Job, Project
from workers.renderers.unreal_validation import SHARE_UNSET_ASSET_MESSAGE

PROJECTS_URL = '/api/projects/'
ASSETS_URL = '/api/assets/'
JOBS_URL = '/api/jobs/'
VALID_BLEND = b'BLENDER' + b'\x00' * 57
BUILD = '5.8.4-58902052'


def _blend(name='scene.blend'):
    return SimpleUploadedFile(name, VALID_BLEND, content_type='application/octet-stream')


def _job(asset, name='WiringJob', **extra):
    return Job.objects.create(
        name=name, asset=asset, output_file_pattern='//render/f_####.png', **extra,
    )


@pytest.fixture
def unreal_project(db):
    return Project.objects.create(name='WiringUnreal', renderer='UNREAL', engine_build=BUILD)


@pytest.fixture
def unreal_asset(unreal_project):
    return Asset.objects.create(
        name='WiringUnrealAsset', project=unreal_project, source_type='shared_project',
        archive_path='incoming/scene.zip', uproject_path='Scene/Scene.uproject',
        archive_sha256='c' * 64,
    )


@pytest.mark.django_db
class TestAdditiveFields:

    def test_project_fields(self, admin_client, default_version):
        resp = admin_client.post(PROJECTS_URL, {'name': 'WiringProj'}, format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['renderer'] == 'BLENDER'
        assert resp.data['engine_build'] == ''
        assert resp.data['unreal_cvar_overrides'] == {}

    def test_asset_fields(self, admin_client, project):
        resp = admin_client.post(ASSETS_URL, {
            'name': 'WiringAsset', 'project': str(project.pk), 'blend_file': _blend(),
        }, format='multipart')
        assert resp.status_code == 201, resp.data
        assert resp.data['source_type'] == 'blend_file'
        for field in ('archive_path', 'uproject_path', 'archive_sha256', 'overlay_path', 'overlay_sha256'):
            assert resp.data[field] == ''
        assert resp.data['project_details']['renderer'] == 'BLENDER'

    def test_job_fields(self, admin_client, asset):
        resp = admin_client.post(JOBS_URL, {
            'name': 'WiringJob1', 'asset_id': asset.pk, 'output_file_pattern': '//r/f_####.png',
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert (resp.data['renderer'], resp.data['engine_build'], resp.data['routing_note']) == ('BLENDER', '', None)
        assert resp.data['asset']['source_type'] == 'blend_file'

    def test_job_renderer_is_read_only(self, admin_client, asset):
        resp = admin_client.post(JOBS_URL, {
            'name': 'WiringJob2', 'asset_id': asset.pk, 'output_file_pattern': '//r/f.png',
            'renderer': 'UNREAL',
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert resp.data['renderer'] == 'BLENDER'

    def test_worker_fields_without_boot_id(self, admin_client, worker_with_token):
        resp = admin_client.get('/api/heartbeat/')
        assert resp.status_code == 200
        row = resp.data[0]
        assert (row['unreal_capabilities'], row['gpu_memory']) == ({}, {})
        assert 'boot_id' not in row


@pytest.mark.django_db
class TestImmutability:

    def test_project_renderer_cannot_change(self, admin_client, project):
        resp = admin_client.patch(f'{PROJECTS_URL}{project.pk}/', {'renderer': 'UNREAL'}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'renderer': ["Renderer cannot be changed after creation."]}

    def test_project_same_renderer_is_allowed(self, admin_client, project):
        resp = admin_client.patch(f'{PROJECTS_URL}{project.pk}/', {'renderer': 'BLENDER'}, format='json')
        assert resp.status_code == 200, resp.data

    def test_asset_source_type_cannot_change(self, admin_client, asset):
        resp = admin_client.patch(f'{ASSETS_URL}{asset.pk}/', {'source_type': 'shared_project'}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'source_type': ["Source type cannot be changed after creation."]}

    def test_asset_cannot_move_to_other_renderer_project(self, admin_client, asset, unreal_project):
        resp = admin_client.patch(f'{ASSETS_URL}{asset.pk}/', {'project': str(unreal_project.pk)}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'project': ["Cannot move an asset to a project of a different renderer."]}

    def test_delivery_field_frozen_once_a_job_references_the_asset(self, admin_client, asset):
        _job(asset)
        resp = admin_client.patch(f'{ASSETS_URL}{asset.pk}/', {'archive_path': 'x.zip'}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'archive_path': ["Cannot change archive_path while jobs reference this asset."]}

    def test_delivery_field_without_jobs_reaches_the_strategy(self, admin_client, asset):
        resp = admin_client.patch(f'{ASSETS_URL}{asset.pk}/', {'archive_path': 'x.zip'}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'archive_path': ["Not applicable to .blend file assets."]}

    def test_job_engine_build_is_create_only(self, admin_client, asset):
        job = _job(asset)
        resp = admin_client.patch(f'{JOBS_URL}{job.pk}/', {'engine_build': BUILD}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'engine_build': ["Engine build cannot be changed after creation."]}

    def test_job_cannot_move_to_asset_of_other_renderer(self, admin_client, asset, unreal_asset):
        job = _job(asset)
        resp = admin_client.patch(f'{JOBS_URL}{job.pk}/', {'asset_id': unreal_asset.pk}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'asset_id': ["Cannot move a job to an asset of a different renderer."]}

    def test_blender_to_blender_asset_move_is_allowed(self, admin_client, asset, project):
        other = Asset.objects.create(name='WiringOther', project=project, blend_file=_blend())
        job = _job(asset)
        resp = admin_client.patch(f'{JOBS_URL}{job.pk}/', {'asset_id': other.pk}, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert job.asset_id == other.pk


@pytest.mark.django_db
class TestBlenderRejectsUnrealKeys:

    def test_project_engine_build(self, admin_client, default_version):
        resp = admin_client.post(PROJECTS_URL, {'name': 'WiringPB', 'engine_build': BUILD}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'engine_build': ["Not applicable to Blender projects."]}

    def test_project_cvar_overrides(self, admin_client, default_version):
        resp = admin_client.post(PROJECTS_URL, {
            'name': 'WiringPC', 'unreal_cvar_overrides': {'r.Streaming.PoolSize': '3000'},
        }, format='json')
        assert resp.status_code == 400
        assert resp.data == {'unreal_cvar_overrides': ["Not applicable to Blender projects."]}

    def test_job_engine_build(self, admin_client, asset):
        resp = admin_client.post(JOBS_URL, {
            'name': 'WiringJB', 'asset_id': asset.pk, 'output_file_pattern': '//r/f.png',
            'engine_build': BUILD,
        }, format='json')
        assert resp.status_code == 400
        assert resp.data == {'engine_build': ["Not applicable to Blender jobs."]}

    def test_asset_archive_fields(self, admin_client, project):
        resp = admin_client.post(ASSETS_URL, {
            'name': 'WiringAA', 'project': str(project.pk), 'blend_file': _blend(),
            'archive_path': 'incoming/x.zip',
        }, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'archive_path': ["Not applicable to .blend file assets."]}

    def test_blend_asset_on_unreal_project(self, admin_client, unreal_project):
        resp = admin_client.post(ASSETS_URL, {
            'name': 'WiringAU', 'project': str(unreal_project.pk), 'blend_file': _blend(),
        }, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'source_type': ["Uploaded .blend file assets require a Blender project."]}


@pytest.mark.django_db
class TestUnrealThroughStrategy:
    """Unreal creates go through the Unreal strategy and never return 500.

    The detailed rules are in ``test_unreal_projects_api.py``,
    ``test_unreal_assets_api.py`` and ``test_unreal_jobs_api.py``.
    """

    def test_unreal_project_create(self, admin_client):
        resp = admin_client.post(PROJECTS_URL, {
            'name': 'WiringUP', 'renderer': 'UNREAL', 'engine_build': BUILD,
        }, format='json')
        assert resp.status_code == 201, resp.data
        assert (resp.data['renderer'], resp.data['blender_version']) == ('UNREAL', None)

    def test_unknown_renderer_is_a_choice_error(self, admin_client):
        resp = admin_client.post(PROJECTS_URL, {'name': 'WiringUX', 'renderer': 'MAYA'}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'renderer': ['"MAYA" is not a valid choice.']}

    def test_shared_project_asset_create_does_not_require_blend_file(self, admin_client, unreal_project):
        resp = admin_client.post(ASSETS_URL, {
            'name': 'WiringUA', 'project': str(unreal_project.pk), 'source_type': 'shared_project',
            'archive_path': 'incoming/x.zip', 'uproject_path': 'X/X.uproject', 'archive_sha256': 'd' * 64,
        }, format='multipart')
        # No blend_file error: the share check (unset here) is what refuses it.
        assert resp.status_code == 400
        assert resp.data == {'archive_path': [SHARE_UNSET_ASSET_MESSAGE]}

    def test_job_on_unreal_asset(self, admin_client, unreal_asset):
        resp = admin_client.post(JOBS_URL, {
            'name': 'WiringUJ', 'asset_id': unreal_asset.pk, 'output_file_pattern': '//r/f.png',
        }, format='json')
        assert resp.status_code == 400
        assert set(resp.data) == {'render_settings'}

    def test_untrimmed_path_fields(self):
        from workers.serializers import AssetSerializer
        fields = AssetSerializer().fields
        for name in ('archive_path', 'overlay_path', 'uproject_path'):
            assert fields[name].trim_whitespace is False


@pytest.mark.django_db
class TestBlendFileRequiredness:

    def test_blend_file_required_by_default(self, admin_client, project):
        resp = admin_client.post(ASSETS_URL, {'name': 'WiringRQ', 'project': str(project.pk)}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'blend_file': ['No file was submitted.']}

    def test_get_fields_follows_instance_and_list_shapes(self, asset):
        from workers.serializers import AssetSerializer
        assert AssetSerializer(asset).fields['blend_file'].required is True
        many = AssetSerializer(Asset.objects.all(), many=True)
        assert many.child.fields['blend_file'].required is True
        shared = AssetSerializer(data={'source_type': 'shared_project'})
        assert shared.fields['blend_file'].required is False
