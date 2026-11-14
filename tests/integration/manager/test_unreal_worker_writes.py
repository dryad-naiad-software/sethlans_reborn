# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: worker writes on Unreal jobs (Unreal foundation FR-RT-14,
FR-T-3, AC-44c).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from workers.models import Job, JobStatus

from .unreal_helpers import RENDER_SETTINGS, unreal_asset

URL = '/api/jobs/'
FORBIDDEN = "You do not have permission to modify this job."


def _rendering(asset, worker, name='WriteJob'):
    job = Job.objects.create(name=name, asset=asset, output_file_pattern='//r/f.png',
                             render_settings=dict(RENDER_SETTINGS), render_device='GPU')
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker)
    return job


@pytest.mark.django_db
class TestOwnership:

    def test_patch_and_upload_by_another_worker_are_403(self, worker_with_token, make_worker_client):
        worker, _client = worker_with_token
        _other, other_client, _token = make_worker_client('writes-other')
        job = _rendering(unreal_asset(), worker)
        for body in ({'status': 'DONE'}, {'render_settings': {}}):
            resp = other_client.patch(f'{URL}{job.pk}/', body, format='json')
            assert (resp.status_code, resp.data) == (403, {'detail': FORBIDDEN})
        png = SimpleUploadedFile('f.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 32, content_type='image/png')
        resp = other_client.post(f'{URL}{job.pk}/upload_output/', {'output_file': png}, format='multipart')
        assert resp.status_code == 403
        assert Job.objects.get(pk=job.pk).status == JobStatus.RENDERING


@pytest.mark.django_db
class TestAllowlist:

    @pytest.mark.parametrize('body, keys', [
        ({'render_settings': {}}, 'render_settings'),
        ({'engine_build': '5.8.4-1', 'status': 'DONE'}, 'engine_build'),
        ({'started_at': '2026-11-14T00:00:00Z'}, 'started_at'),
        ({'start_frame': 5, 'name': 'x'}, 'name, start_frame'),
    ])
    def test_refused_keys_get_the_allowlist_detail(self, worker_with_token, body, keys):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = client.patch(f'{URL}{job.pk}/', body, format='json')
        assert resp.status_code == 400
        assert resp.data == {'detail': f"Workers may not modify {keys} on Unreal jobs."}
        assert Job.objects.get(pk=job.pk).status == JobStatus.RENDERING

    @pytest.mark.parametrize('body', [[], ['status'], 'DONE'])
    def test_non_object_body_is_400(self, worker_with_token, body):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = client.patch(f'{URL}{job.pk}/', body, format='json')
        assert (resp.status_code, resp.data) == (400, {'detail': "Expected an object."})
        assert Job.objects.get(pk=job.pk).status == JobStatus.RENDERING

    def test_allowed_status_patch(self, worker_with_token):
        worker, client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = client.patch(f'{URL}{job.pk}/', {'status': 'ERROR', 'error_message': 'UE crashed'}, format='json')
        assert resp.status_code == 200, resp.data

    def test_admin_patch_is_not_restricted(self, admin_client, worker_with_token):
        worker, _client = worker_with_token
        job = _rendering(unreal_asset(), worker)
        resp = admin_client.patch(f'{URL}{job.pk}/', {'name': 'AdminRename'}, format='json')
        assert resp.status_code == 200, resp.data


@pytest.mark.django_db
class TestBlenderUnchanged:

    def test_no_allowlist_for_blender(self, worker_with_token, asset):
        worker, client = worker_with_token
        job = _rendering(asset, worker, 'BlenderWrite')
        Job.objects.filter(pk=job.pk).update(render_settings={})
        resp = client.patch(f'{URL}{job.pk}/', {'started_at': '2026-11-14T00:00:00Z'}, format='json')
        # #217's own refusal, not the Unreal allowlist message.
        assert resp.status_code == 400
        assert resp.data == {'started_at': ["Workers cannot change this field."]}

    def test_blender_non_owner_keeps_403(self, worker_with_token, make_worker_client, asset):
        worker, _client = worker_with_token
        _other, other_client, _token = make_worker_client('writes-blender-other')
        job = _rendering(asset, worker, 'BlenderOwner')
        Job.objects.filter(pk=job.pk).update(render_settings={})
        resp = other_client.patch(f'{URL}{job.pk}/', {'status': 'DONE'}, format='json')
        assert (resp.status_code, resp.data) == (403, {'detail': FORBIDDEN})
