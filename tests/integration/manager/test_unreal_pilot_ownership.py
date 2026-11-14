# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: pilot jobs are manager-owned, and Unreal jobs/assets
keep their links (Unreal foundation FR-PM-9, FR-RT-14, FR-MR-11, AC-47,
AC-56).
"""

import pytest

from workers.models import Job, JobStatus
from workers.services.worker_job_writes import PilotDoneRefused, apply_admin_patch, apply_worker_patch

from .unreal_helpers import unreal_asset, unreal_project
from .unreal_pilot_helpers import JOBS, claimed_pilot, created_job, pilots, record_of, settings


@pytest.fixture
def asset(db):
    return unreal_asset()


def _done_message(job):
    return {'detail': f"Pilot jobs complete through POST /api/jobs/{job.pk}/pilot-result/."}


def _failed_status(admin_client, project_id):
    data = admin_client.get(f'/api/projects/{project_id}/').data['unreal_measurement']
    return data['latest']['status'], data['latest']['reason']


@pytest.mark.django_db
class TestDoneOnlyThroughPilotResult:

    def test_worker_patch_done_is_400(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        resp = client.patch(f'{JOBS}{pilot.pk}/', {'status': 'DONE'}, format='json')
        assert (resp.status_code, resp.data) == (400, _done_message(pilot))
        pilot.refresh_from_db()
        assert pilot.status == 'RENDERING'

    def test_worker_patch_error_fails_the_measurement(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        resp = client.patch(f'{JOBS}{pilot.pk}/', {'status': 'ERROR', 'error_message': 'Pilot render failed: x'},
                            format='json')
        assert resp.status_code == 200, resp.data
        assert _failed_status(admin_client, asset.project_id) == ('failed', 'Pilot render failed: x')

    def test_admin_patch_and_put_done_are_400(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        resp = admin_client.patch(f'{JOBS}{pilot.pk}/', {'status': 'DONE'}, format='json')
        assert (resp.status_code, resp.data) == (400, _done_message(pilot))
        body = {'name': pilot.name, 'asset_id': asset.pk, 'output_file_pattern': pilot.output_file_pattern,
                'start_frame': pilot.start_frame, 'end_frame': pilot.end_frame,
                'render_settings': pilot.render_settings, 'status': 'DONE'}
        resp = admin_client.put(f'{JOBS}{pilot.pk}/', body, format='json')
        assert (resp.status_code, resp.data) == (400, _done_message(pilot))
        pilot.refresh_from_db()
        assert pilot.status == 'RENDERING'

    def test_services_refuse_done_directly(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        with pytest.raises(PilotDoneRefused):
            apply_admin_patch(pilot.pk, {'status': JobStatus.DONE})
        with pytest.raises(PilotDoneRefused):
            apply_worker_patch(pilot.pk, worker, {'status': JobStatus.DONE})
        pilot.refresh_from_db()
        assert pilot.status == 'RENDERING'

    def test_orm_done_without_result_reads_failed(self, admin_client, asset):
        created_job(admin_client, asset)
        Job.objects.filter(pk=pilots()[0].pk).update(status='DONE')
        assert _failed_status(admin_client, asset.project_id) == (
            'failed', 'Pilot job finished without a measurement.')


@pytest.mark.django_db
class TestPilotWrites:

    @pytest.fixture
    def pilot(self, admin_client, asset):
        created_job(admin_client, asset)
        return pilots()[0]

    def test_requeue_is_409(self, admin_client, pilot):
        Job.objects.filter(pk=pilot.pk).update(status='ERROR')
        resp = admin_client.post(f'{JOBS}{pilot.pk}/requeue/')
        assert (resp.status_code, resp.data) == (409, {
            'error': f"Use POST /api/projects/{pilot.asset.project_id}/unreal-measurement/retry/ to measure again."})

    @pytest.mark.parametrize('field, value', [
        ('render_settings', settings(sequence='/Game/Cinematics/Other.Other')),
        ('start_frame', 5), ('end_frame', 5), ('engine_build', '5.8.3-1'),
    ])
    def test_target_fields_are_refused(self, admin_client, pilot, field, value):
        resp = admin_client.patch(f'{JOBS}{pilot.pk}/', {field: value}, format='json')
        assert (resp.status_code, resp.data) == (400, {field: [f"A pilot job's {field} cannot be changed."]})

    def test_asset_is_refused(self, admin_client, pilot):
        other = unreal_asset(pilot.asset.project, name='Other', archive_path='incoming/o.zip')
        resp = admin_client.patch(f'{JOBS}{pilot.pk}/', {'asset_id': other.pk}, format='json')
        assert (resp.status_code, resp.data) == (400, {'asset': ["A pilot job's asset cannot be changed."]})

    def test_unchanged_target_fields_are_accepted(self, admin_client, pilot):
        resp = admin_client.patch(f'{JOBS}{pilot.pk}/', {'render_settings': pilot.render_settings,
                                                         'start_frame': pilot.start_frame}, format='json')
        assert resp.status_code == 200, resp.data

    def test_cancel_and_delete_make_the_record_failed(self, admin_client, asset, pilot):
        assert admin_client.post(f'{JOBS}{pilot.pk}/cancel/').status_code == 200
        assert _failed_status(admin_client, asset.project_id) == ('failed', 'Pilot job was canceled.')
        record = record_of(pilot)
        assert admin_client.delete(f'{JOBS}{pilot.pk}/').status_code == 204
        record.refresh_from_db()
        assert record.pilot_job_id is None
        assert _failed_status(admin_client, asset.project_id) == ('failed', 'Pilot job was deleted.')


@pytest.mark.django_db
class TestCreateOnlyLinks:

    def test_unreal_job_cannot_move_to_another_asset(self, admin_client, asset):
        job = created_job(admin_client, asset)
        other = unreal_asset(asset.project, name='Other', archive_path='incoming/o.zip')
        resp = admin_client.patch(f'{JOBS}{job.pk}/', {'asset_id': other.pk}, format='json')
        assert (resp.status_code, resp.data) == (400, {'asset_id': ["Cannot move an Unreal job to another asset."]})

    def test_shared_asset_cannot_move_to_another_project(self, admin_client, asset):
        other = unreal_project(name='OtherProj')
        resp = admin_client.patch(f'/api/assets/{asset.pk}/', {'project': str(other.pk)}, format='multipart')
        assert (resp.status_code, resp.data) == (400, {'project': ["Cannot move an Unreal asset to another project."]})

    def test_same_asset_and_project_are_accepted(self, admin_client, asset):
        job = created_job(admin_client, asset)
        resp = admin_client.patch(f'{JOBS}{job.pk}/', {'asset_id': asset.pk}, format='json')
        assert resp.status_code == 200, resp.data
        resp = admin_client.patch(f'/api/assets/{asset.pk}/', {'project': str(asset.project_id)},
                                  format='multipart')
        assert resp.status_code == 200, resp.data

    def test_blender_job_may_still_move_to_another_blender_asset(self, admin_client, asset_on_blender):
        first, second = asset_on_blender
        job = Job.objects.create(name='BlendMove', asset=first, output_file_pattern='//r/b.png')
        resp = admin_client.patch(f'{JOBS}{job.pk}/', {'asset_id': second.pk}, format='json')
        assert resp.status_code == 200, resp.data


@pytest.fixture
def asset_on_blender(project):
    from workers.models import Asset
    return (Asset.objects.create(name='BlendA', project=project, blend_file=b'BLENDER-v300'),
            Asset.objects.create(name='BlendB', project=project, blend_file=b'BLENDER-v300'))


@pytest.mark.django_db
class TestRelease:

    def test_local_conflict_is_a_release_reason(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        resp = client.post(f'{JOBS}{pilot.pk}/release/', {'reason': 'local_conflict'}, format='json')
        assert (resp.status_code, resp.data) == (200, {'status': 'QUEUED', 'auto_requeue_count': 1})

    def test_requeue_limit_fails_the_measurement(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        for attempt in range(4):
            if attempt:
                assert client.post(f'{JOBS}{pilot.pk}/claim/', {'worker_id': worker.pk},
                                   format='json').status_code == 200
            resp = client.post(f'{JOBS}{pilot.pk}/release/', {'reason': 'shutdown'}, format='json')
            assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'ERROR'
        assert _failed_status(admin_client, asset.project_id) == (
            'failed', 'Max auto-requeue attempts exceeded (3)')


@pytest.mark.django_db
class TestNoRevival:
    """A failed or canceled pilot is never set QUEUED again (one pending pilot per key)."""

    @pytest.mark.parametrize('status', ['ERROR', 'CANCELED'])
    def test_patch_and_put_to_queued_are_409(self, admin_client, asset, status):
        created_job(admin_client, asset)
        pilot = pilots()[0]
        Job.objects.filter(pk=pilot.pk).update(status=status)
        expected = {'error': f"Use POST /api/projects/{asset.project_id}/unreal-measurement/retry/ "
                             "to measure again."}
        resp = admin_client.patch(f'{JOBS}{pilot.pk}/', {'status': 'QUEUED'}, format='json')
        assert (resp.status_code, resp.data) == (409, expected)
        body = {'name': pilot.name, 'asset_id': asset.pk, 'output_file_pattern': pilot.output_file_pattern,
                'start_frame': pilot.start_frame, 'end_frame': pilot.end_frame,
                'render_settings': pilot.render_settings, 'status': 'QUEUED'}
        resp = admin_client.put(f'{JOBS}{pilot.pk}/', body, format='json')
        assert (resp.status_code, resp.data) == (409, expected)
        pilot.refresh_from_db()
        assert pilot.status == status
        assert Job.objects.filter(unreal_kind='pilot', status='QUEUED').count() == 0

    def test_worker_patch_service_refuses_queued(self, admin_client, asset, worker_with_token):
        from workers.services.worker_job_writes import JobWriteConflict
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        with pytest.raises(JobWriteConflict, match='unreal-measurement/retry'):
            apply_worker_patch(pilot.pk, worker, {'status': JobStatus.QUEUED})


@pytest.mark.django_db
class TestLockedDelete:
    """Deletes lock the job rows first (Job -> UnrealMeasurement, as pilot-result/ does)."""

    def test_pilot_delete_locks_the_job_first(self, admin_client, asset, mocker):
        from workers.views import job_pilot_actions
        created_job(admin_client, asset)
        pilot = pilots()[0]
        spy = mocker.spy(job_pilot_actions, 'lock_own_rows')
        delete = mocker.spy(Job, 'delete')
        assert admin_client.delete(f'{JOBS}{pilot.pk}/').status_code == 204
        assert spy.call_count == 1 and spy.call_args.args[0].model is Job
        assert delete.call_count == 1
        assert not Job.objects.filter(pk=pilot.pk).exists()

    def test_asset_delete_locks_its_jobs_by_pk_first(self, admin_client, asset, mocker):
        from workers.views import assets as asset_views
        created_job(admin_client, asset)
        spy = mocker.spy(asset_views, 'lock_own_rows')
        assert admin_client.delete(f'/api/assets/{asset.pk}/').status_code == 204
        locked = spy.call_args.args[0]
        assert locked.model is Job and locked.query.order_by == ('pk',)
        assert not Job.objects.filter(asset_id=asset.pk).exists()

    def test_blender_deletes_take_no_lock(self, admin_client, asset_on_blender, mocker):
        from workers.views import assets as asset_views, job_pilot_actions
        first, _second = asset_on_blender
        job = Job.objects.create(name='BlendDel', asset=first, output_file_pattern='//r/b.png')
        job_spy = mocker.spy(job_pilot_actions, 'lock_own_rows')
        asset_spy = mocker.spy(asset_views, 'lock_own_rows')
        assert admin_client.delete(f'{JOBS}{job.pk}/').status_code == 204
        assert admin_client.delete(f'/api/assets/{first.pk}/').status_code == 204
        assert job_spy.call_count == 0 and asset_spy.call_count == 0


@pytest.mark.django_db
def test_django_admin_cannot_steer_a_pilot(admin_client, asset):
    from django.contrib import admin as django_admin

    from workers.admin import JobAdmin
    created_job(admin_client, asset)
    normal, pilot = Job.objects.get(name='Still01'), pilots()[0]
    job_admin = JobAdmin(Job, django_admin.site)
    assert 'unreal_kind' in job_admin.get_readonly_fields(None, normal)
    assert 'status' not in job_admin.get_readonly_fields(None, normal)
    pilot_fields = set(job_admin.get_readonly_fields(None, pilot))
    assert {'unreal_kind', 'status', 'render_settings', 'start_frame', 'end_frame', 'asset',
            'engine_build'} <= pilot_fields
