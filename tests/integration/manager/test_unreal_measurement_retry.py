# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``POST /api/projects/{id}/unreal-measurement/retry/``
(Unreal foundation FR-PM-6, AC-48).
"""

from unittest import mock

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from workers.models import Job, UnrealMeasurement
from workers.services import unreal_retry

from .unreal_helpers import RENDER_SETTINGS, unreal_asset, unreal_project
from .unreal_pilot_helpers import (
    JOBS, OTHER_SEQUENCE, created_job, measure, pilots, ready_worker, record_of, settings,
)


def _url(project_id):
    return f'/api/projects/{project_id}/unreal-measurement/retry/'


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def failed(admin_client, asset):
    """A held job whose pilot failed."""
    job = created_job(admin_client, asset)
    Job.objects.filter(pk=pilots()[0].pk).update(status='ERROR', error_message='boom')
    return job


@pytest.mark.django_db
class TestRetry:

    def test_after_a_failure_creates_a_new_pilot(self, admin_client, asset, failed, worker_with_token):
        ready_worker(worker_with_token[0])
        with mock.patch.object(unreal_retry, 'logger') as log:
            resp = admin_client.post(_url(asset.project_id), {}, format='json')
        assert resp.status_code == 201, resp.data
        pilot = Job.objects.get(pk=resp.data['pilot_job_id'])
        record = UnrealMeasurement.objects.get(pk=resp.data['measurement_id'])
        assert record.pilot_job_id == pilot.pk and record.remeasure is False
        assert pilot.unreal_target_sha256 == failed.unreal_target_sha256
        assert pilot.submitted_at == failed.submitted_at
        message = log.warning.call_args.args[0] % log.warning.call_args.args[1:]
        assert message.startswith(f"Unreal measurement retry by admin for project {asset.project_id}, asset {asset.pk}")
        assert message.endswith("(remeasure=False).")
        state = admin_client.get(f'{JOBS}{failed.pk}/').data['routing_note']
        assert state == "Waiting for this project's memory measurement (pilot render)."

    def test_none_state_job(self, admin_client, asset):
        job = Job.objects.create(name='Legacy', asset=asset, output_file_pattern='//r/f.png',
                                 render_settings=dict(RENDER_SETTINGS))
        resp = admin_client.post(_url(asset.project_id), {}, format='json')
        assert resp.status_code == 201, resp.data
        assert Job.objects.get(pk=resp.data['pilot_job_id']).unreal_target_sha256 == job.unreal_target_sha256

    def test_pending_is_409(self, admin_client, asset):
        job = created_job(admin_client, asset)
        resp = admin_client.post(_url(asset.project_id), {'job_id': job.pk}, format='json')
        assert (resp.status_code, resp.data) == (409, {'error': "A pilot render is already queued or running."})

    def test_no_held_job_is_409(self, admin_client, asset):
        created_job(admin_client, asset)
        resp = admin_client.post(_url(asset.project_id), {}, format='json')
        assert (resp.status_code, resp.data) == (409, {'error': "No held Unreal job needs a measurement; pass job_id."})

    def test_measured_needs_remeasure(self, admin_client, asset, worker_with_token):
        ready_worker(worker_with_token[0])
        job = created_job(admin_client, asset)
        old = record_of(pilots()[0])
        measure(old, peak_mb=8102)
        resp = admin_client.post(_url(asset.project_id), {'job_id': job.pk}, format='json')
        assert (resp.status_code, resp.data) == (409, {
            'error': 'This target is already measured; send "remeasure": true to measure it again.'})
        with mock.patch.object(unreal_retry, 'logger') as log:
            resp = admin_client.post(_url(asset.project_id), {'job_id': job.pk, 'remeasure': True}, format='json')
        assert resp.status_code == 201, resp.data
        assert UnrealMeasurement.objects.get(pk=resp.data['measurement_id']).remeasure is True
        assert 'remeasure=True' in log.warning.call_args.args[0] % log.warning.call_args.args[1:]
        # The old peak stays in force until the new pilot succeeds.
        detail = admin_client.get(f'{JOBS}{job.pk}/').data
        assert (detail['unreal_measured_peak_mb'], detail['routing_note']) == (8102, None)
        Job.objects.filter(pk=resp.data['pilot_job_id']).update(status='ERROR', error_message='x')
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_measured_peak_mb'] == 8102

    def test_job_id_targets_that_job(self, admin_client, asset, failed):
        other = created_job(admin_client, asset, name='Other', render_settings=settings(sequence=OTHER_SEQUENCE))
        Job.objects.filter(pk=pilots()[-1].pk).update(status='CANCELED')
        resp = admin_client.post(_url(asset.project_id), {'job_id': other.pk}, format='json')
        assert resp.status_code == 201, resp.data
        pilot = Job.objects.get(pk=resp.data['pilot_job_id'])
        assert pilot.render_settings['unreal.sequence'] == OTHER_SEQUENCE

    def test_job_id_rules(self, admin_client, asset, failed, project):
        from workers.models import Asset
        pilot = pilots()[0]
        other_project_job = created_job(admin_client, unreal_asset(unreal_project(name='Other'), name='OA'))
        blend = Job.objects.create(name='Blend', asset=Asset.objects.create(
            name='BA', project=project, blend_file=b'BLENDER-v300'), output_file_pattern='//r/b.png')
        for job_id in (pilot.pk, other_project_job.pk, blend.pk, 999999):
            resp = admin_client.post(_url(asset.project_id), {'job_id': job_id}, format='json')
            assert (resp.status_code, resp.data) == (400, {'job_id': ["Not an Unreal job of this project."]})

    @pytest.mark.parametrize('body', [{'remeasure': 'yes'}, {'remeasure': 1}, {'job_id': True},
                                      {'job_id': '5'}])
    def test_strict_types(self, admin_client, asset, failed, body):
        resp = admin_client.post(_url(asset.project_id), body, format='json')
        assert resp.status_code == 400 and set(resp.data) == set(body)

    def test_unknown_keys(self, admin_client, asset, failed):
        resp = admin_client.post(_url(asset.project_id), {'asset_id': 1, 'b': 2}, format='json')
        assert (resp.status_code, resp.data) == (400, {'detail': 'Unknown keys: asset_id, b.'})

    def test_blender_project_is_400(self, admin_client, project):
        resp = admin_client.post(_url(project.pk), {}, format='json')
        assert (resp.status_code, resp.data) == (400, {'detail': "Not an Unreal project."})

    def test_project_without_asset_is_409(self, admin_client):
        project = unreal_project(name='Empty')
        resp = admin_client.post(_url(project.pk), {}, format='json')
        assert (resp.status_code, resp.data) == (409, {'error': "This project has no Unreal asset yet."})

    def test_unknown_project_is_404(self, admin_client):
        resp = admin_client.post(_url('00000000-0000-0000-0000-000000000000'), {}, format='json')
        assert resp.status_code == 404

    def test_non_admin_and_worker_are_403(self, asset, failed, worker_with_token):
        _worker, worker_client = worker_with_token
        assert worker_client.post(_url(asset.project_id), {}, format='json').status_code == 403
        user = User.objects.create_user(username='plain', password='x')
        client = APIClient()
        client.force_authenticate(user=user)
        assert client.post(_url(asset.project_id), {}, format='json').status_code == 403
        assert len(pilots()) == 1


@pytest.mark.django_db
def test_audit_logs_the_record_remeasure_flag(admin_client, asset, failed):
    """``remeasure: true`` on a failed key creates a normal record: the log says so."""
    with mock.patch.object(unreal_retry, 'logger') as log:
        resp = admin_client.post(_url(asset.project_id), {'remeasure': True}, format='json')
    assert resp.status_code == 201, resp.data
    assert UnrealMeasurement.objects.get(pk=resp.data['measurement_id']).remeasure is False
    assert 'remeasure=False' in log.warning.call_args.args[0] % log.warning.call_args.args[1:]
