# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``settle_discovery`` (Unreal sequence spec FR-SD-9,
D22, AC-33).

A ``shots`` job that ends ERROR or CANCELED by any path, or is deleted,
fails its discovery after commit.  Covered paths: the worker PATCH, the
job cancel action, the auto-requeue limit of a release, the admin delete.
A crash between commit and callback (the job final, the discovery still
open) is settled by a discovery read, a list read and the start-hook
function.  Settling is idempotent and leaves DONE records alone.
"""

from unittest import mock

import pytest

from workers.models import Job, SequenceDiscovery
from workers.services.sequence_discovery import settle_discovery, settle_open_discoveries

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS
from tests._sequence_bodies import shots_body
from .sequence_helpers import DISCOVERIES, claimed_shots_job, post_shots, start_discovery


@pytest.fixture
def asset(db):
    return unreal_asset()


def _state(discovery):
    discovery.refresh_from_db()
    return discovery.status, discovery.error_message


@pytest.mark.django_db
class TestAfterCommit:

    def test_worker_patch_error_fails_the_discovery(self, admin_client, asset, worker_with_token,
                                                    django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        body = {'status': 'ERROR', 'error_message': 'Shot sections a and b overlap; Sethlans cannot tell which one renders.'}
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.patch(f'{JOBS}{job.pk}/', body, format='json')
        assert resp.status_code == 200, resp.data
        assert _state(discovery) == ('ERROR', body['error_message'])

    def test_not_settled_before_commit(self, admin_client, asset, worker_with_token,
                                       django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            client.patch(f'{JOBS}{job.pk}/', {'status': 'ERROR', 'error_message': 'x'}, format='json')
        assert _state(discovery)[0] == 'RUNNING'
        for callback in callbacks:
            callback()
        assert _state(discovery) == ('ERROR', 'x')

    def test_cancel_fails_the_discovery(self, admin_client, asset, django_capture_on_commit_callbacks):
        discovery, _ = start_discovery(admin_client, asset)
        with django_capture_on_commit_callbacks(execute=True):
            assert admin_client.post(f'{JOBS}{discovery.job_id}/cancel/').status_code == 200
        assert _state(discovery) == ('ERROR', 'The shots job was canceled.')

    def test_release_over_the_limit_fails_the_discovery(self, admin_client, asset, worker_with_token,
                                                        django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        Job.objects.filter(pk=job.pk).update(auto_requeue_count=3)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(f'{JOBS}{job.pk}/release/', {'reason': 'dispatch_failed'}, format='json')
        assert resp.data['status'] == 'ERROR'
        status, reason = _state(discovery)
        assert status == 'ERROR' and reason

    def test_admin_delete_fails_the_discovery(self, admin_client, asset, django_capture_on_commit_callbacks):
        discovery, _ = start_discovery(admin_client, asset)
        with django_capture_on_commit_callbacks(execute=True):
            assert admin_client.delete(f'{JOBS}{discovery.job_id}/').status_code == 204
        assert _state(discovery) == ('ERROR', 'The shots job was deleted.')
        assert discovery.job_id is None

    def test_a_new_post_after_failure_starts_again(self, admin_client, asset, django_capture_on_commit_callbacks):
        first, _ = start_discovery(admin_client, asset)
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(f'{JOBS}{first.job_id}/cancel/')
        second, _ = start_discovery(admin_client, asset, expect=201)
        assert second.pk != first.pk and second.status == 'QUEUED'


@pytest.mark.django_db
class TestCrashRecovery:
    """The job is final but the callback never ran (``QuerySet.update`` skips it)."""

    @pytest.fixture
    def stranded(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).update(status='ERROR', error_message='Prep timed out.')
        return discovery

    def test_settled_by_a_discovery_read(self, admin_client, stranded):
        data = admin_client.get(f'{DISCOVERIES}{stranded.pk}/').data
        assert (data['status'], data['error_message']) == ('ERROR', 'Prep timed out.')

    def test_settled_by_a_list_read(self, admin_client, stranded):
        data = admin_client.get(DISCOVERIES).data['results']
        assert [(row['id'], row['status']) for row in data] == [(stranded.pk, 'ERROR')]

    def test_settled_by_the_start_hook_function(self, stranded):
        assert settle_open_discoveries() == 1
        assert _state(stranded) == ('ERROR', 'Prep timed out.')
        assert settle_open_discoveries() == 0

    def test_deleted_job_settled_by_a_read(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).delete()
        data = admin_client.get(f'{DISCOVERIES}{discovery.pk}/').data
        assert (data['status'], data['error_message']) == ('ERROR', 'The shots job was deleted.')

    def test_one_bad_row_does_not_stop_the_others(self, admin_client, asset, stranded):
        other_asset = unreal_asset(project=asset.project, name='Second', archive_sha256='ef' * 32)
        other, _ = start_discovery(admin_client, other_asset)
        Job.objects.filter(pk=other.job_id).update(status='CANCELED')
        real = settle_discovery
        with mock.patch('workers.services.sequence_discovery.settle_discovery',
                        side_effect=lambda pk: (_ for _ in ()).throw(RuntimeError('x')) if pk == stranded.pk
                        else real(pk)):
            assert settle_open_discoveries() == 1
        assert _state(other)[0] == 'ERROR'
        assert _state(stranded)[0] == 'QUEUED'


@pytest.mark.django_db
class TestIdempotent:

    def test_active_job_is_left_alone(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        assert settle_discovery(discovery.pk) is False
        assert _state(discovery)[0] == 'QUEUED'
        assert admin_client.get(f'{DISCOVERIES}{discovery.pk}/').data['status'] == 'QUEUED'

    def test_second_settle_changes_nothing(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).update(status='CANCELED')
        assert settle_discovery(discovery.pk) is True
        SequenceDiscovery.objects.filter(pk=discovery.pk).update(error_message='kept')
        assert settle_discovery(discovery.pk) is False
        assert _state(discovery) == ('ERROR', 'kept')

    def test_done_record_is_never_failed(self, admin_client, asset, worker_with_token,
                                         django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        with django_capture_on_commit_callbacks(execute=True):
            assert post_shots(client, job.pk, shots_body()).status_code == 200
        Job.objects.filter(pk=job.pk).update(status='ERROR')
        assert settle_discovery(discovery.pk) is False
        assert settle_open_discoveries() == 0
        assert _state(discovery)[0] == 'DONE'

    def test_done_job_without_a_result_fails(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).update(status='DONE')
        assert settle_discovery(discovery.pk) is True
        assert _state(discovery) == ('ERROR', 'The shots job finished without a result.')

    def test_unknown_discovery(self, db):
        assert settle_discovery(424242) is False

    def test_settle_takes_the_project_lock_first(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        Job.objects.filter(pk=discovery.job_id).update(status='CANCELED')
        order = []
        from workers.services import sequence_discovery as service
        real_lock, real_settle = service.lock_project, service._settle_locked
        with mock.patch.object(service, 'lock_project', side_effect=lambda p: order.append('project') or real_lock(p)), \
                mock.patch.object(service, '_settle_locked',
                                  side_effect=lambda pk: order.append('discovery') or real_settle(pk)):
            assert settle_discovery(discovery.pk) is True
        assert order == ['project', 'discovery']


@pytest.mark.django_db
class TestReviewRound:

    def test_list_settles_only_the_rows_of_its_page(self, admin_client, asset):
        stale = []
        for sequence in ('/Game/C/A.A', '/Game/C/B.B', '/Game/C/D.D'):
            discovery, _ = start_discovery(admin_client, asset, sequence=sequence)
            Job.objects.filter(pk=discovery.job_id).update(status='CANCELED')
            stale.append(discovery)
        with mock.patch('workers.views.sequence_discoveries.settle_discovery',
                        side_effect=settle_discovery) as settle:
            rows = admin_client.get(DISCOVERIES, {'page_size': 1}).data['results']
        assert settle.call_count == 1
        assert [(row['id'], row['status']) for row in rows] == [(stale[-1].pk, 'ERROR')]
        assert [_state(d)[0] for d in stale[:2]] == ['QUEUED', 'QUEUED']

    def test_project_cancel_all_settles_the_discovery(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        resp = admin_client.post(f'/api/projects/{asset.project_id}/cancel_all_jobs/')
        assert resp.data == {'canceled': 1}
        assert _state(discovery) == ('ERROR', 'The shots job was canceled.')

    def test_project_cancel_all_takes_the_project_lock_first(self, admin_client, asset):
        discovery, _ = start_discovery(admin_client, asset)
        order = []
        from workers.services import sequence_discovery as service
        from workers.services import unreal_pilot
        real_lock, real_settle = unreal_pilot.lock_project, service._settle_locked
        with mock.patch.object(unreal_pilot, 'lock_project',
                               side_effect=lambda p: order.append('project') or real_lock(p)), \
                mock.patch.object(service, '_settle_locked',
                                  side_effect=lambda pk: order.append('discovery') or real_settle(pk)):
            admin_client.post(f'/api/projects/{asset.project_id}/cancel_all_jobs/')
        assert order == ['project', 'discovery']
        assert _state(discovery)[0] == 'ERROR'

    def test_project_cancel_all_leaves_other_projects_alone(self, admin_client, asset):
        from ..unreal_helpers import unreal_project
        other_asset = unreal_asset(project=unreal_project(name='Other'), name='OtherArchive')
        other, _ = start_discovery(admin_client, other_asset)
        start_discovery(admin_client, asset)
        admin_client.post(f'/api/projects/{asset.project_id}/cancel_all_jobs/')
        assert _state(other)[0] == 'QUEUED'

    def test_done_save_queues_no_settle(self, admin_client, asset, worker_with_token,
                                        django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        discovery, job = claimed_shots_job(admin_client, asset, worker, client)
        with mock.patch('workers.services.sequence_discovery.settle_for_job') as settle, \
                django_capture_on_commit_callbacks(execute=True):
            assert post_shots(client, job.pk, shots_body()).status_code == 200
            job.refresh_from_db()
            job.save()
        settle.assert_not_called()
        assert _state(discovery)[0] == 'DONE'
