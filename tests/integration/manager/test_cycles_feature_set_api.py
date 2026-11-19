# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: no experimental Cycles feature set on Blender 5.0+ (#244).

Creating a Job, TiledJob or Animation with ``EXPERIMENTAL`` on a job that
renders with Blender 5.0 or later (its own ``blender_version``, else its
asset's project's) is a 400 field error on ``cycles_feature_set``.
``SUPPORTED`` is valid on every version; 4.x keeps both values. A worker's
own writes to a stored row are never refused, and Unreal jobs keep their
own rule. Updates are in ``test_cycles_feature_set_patch.py``, the child
jobs' ``render_settings`` in ``test_cycles_feature_set_dispatch.py``.
"""

import pytest

from workers.models import Job, JobStatus, Worker

from .cycles_feature_set_helpers import (
    JOBS_URL, KINDS, blend_asset, created_pk, expected_message, feature_set_error, post, version,
)
from .unreal_helpers import RENDER_SETTINGS, unreal_asset

ALL_KINDS = list(KINDS)


@pytest.fixture
def asset52(db):
    """An asset whose project renders with Blender 5.2."""
    return blend_asset('5.2', 'Asset52')


def _assert_nothing_created(kind):
    _, _, model = KINDS[kind]
    assert not model.objects.exists()
    assert not Job.objects.exists()


@pytest.mark.django_db
class TestCreateRefused:

    @pytest.mark.parametrize('kind', ALL_KINDS)
    @pytest.mark.parametrize('series', ['5.0', '5.2'])
    def test_experimental_with_5x_job_version(self, admin_client, asset, kind, series):
        resp = post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL',
                    blender_version=version(series).pk)
        message = feature_set_error(resp)
        assert message == expected_message(series)
        assert f'Blender {series}' in message
        _assert_nothing_created(kind)

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_on_5x_project_without_job_version(self, admin_client, asset52, kind):
        resp = post(admin_client, kind, asset52, cycles_feature_set='EXPERIMENTAL')
        assert feature_set_error(resp) == expected_message('5.2')
        _assert_nothing_created(kind)

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_with_explicit_null_version_on_5x_project(self, admin_client, asset52, kind):
        resp = post(admin_client, kind, asset52, cycles_feature_set='EXPERIMENTAL',
                    blender_version=None)
        assert feature_set_error(resp) == expected_message('5.2')

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_5x_job_version_wins_over_4x_project(self, admin_client, asset, kind):
        # ``asset`` is on the 4.2 project: the job's own 5.0 is what renders.
        resp = post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL',
                    blender_version=version('5.0').pk)
        assert 'Blender 5.0)' in feature_set_error(resp)


@pytest.mark.django_db
class TestCreateAllowed:

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_supported_on_5x_job_version(self, admin_client, asset, kind):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='SUPPORTED',
                             blender_version=version('5.2').pk))
        assert KINDS[kind][2].objects.get(pk=pk).cycles_feature_set == 'SUPPORTED'

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_supported_on_5x_project(self, admin_client, asset52, kind):
        created_pk(post(admin_client, kind, asset52, cycles_feature_set='SUPPORTED'))

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_field_omitted_on_5x_project(self, admin_client, asset52, kind):
        resp = post(admin_client, kind, asset52)
        created_pk(resp)
        assert resp.data['cycles_feature_set'] == 'SUPPORTED'

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_on_4x_project(self, admin_client, asset, kind):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL'))
        assert KINDS[kind][2].objects.get(pk=pk).cycles_feature_set == 'EXPERIMENTAL'

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_with_4x_job_version_on_4x_project(self, admin_client, asset, kind):
        created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL',
                        blender_version=version('4.5').pk))

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_with_4x_job_version_on_5x_project(self, admin_client, asset52, kind):
        resp = post(admin_client, kind, asset52, cycles_feature_set='EXPERIMENTAL',
                    blender_version=version('4.5').pk)
        pk = created_pk(resp)
        row = KINDS[kind][2].objects.get(pk=pk)
        assert (row.cycles_feature_set, row.blender_version.series) == ('EXPERIMENTAL', '4.5')
        assert resp.data['effective_blender_version']['series'] == '4.5'


@pytest.fixture
def stored_job(worker_with_token, asset52):
    """An EXPERIMENTAL job on Blender 5.2 written through the ORM (a pre-#244 row)."""
    worker, client = worker_with_token
    job = Job.objects.create(
        name='LegacyExperimental', asset=asset52, output_file_pattern='render_####.png',
        start_frame=1, end_frame=1, cycles_feature_set='EXPERIMENTAL',
        status=JobStatus.RENDERING, assigned_worker=worker,
    )
    return job, client


@pytest.mark.django_db
class TestWorkerWritesOnStoredRow:
    """The worker sends no feature set, version or asset, so nothing is refused."""

    def test_final_done_report(self, stored_job):
        job, client = stored_job
        # The fields the worker's final report sends (job_report.py).
        resp = client.patch(f'{JOBS_URL}{job.pk}/', data={
            'completed_at': '2026-11-18T00:00:00Z', 'last_output': 'Fra:1 Saved render_0001.png',
            'error_message': '', 'render_time_seconds': 12, 'status': 'DONE',
        }, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.cycles_feature_set) == (JobStatus.DONE, 'EXPERIMENTAL')

    def test_error_report(self, stored_job):
        job, client = stored_job
        # The fields the worker's failure report sends (job_lifecycle.py).
        resp = client.patch(f'{JOBS_URL}{job.pk}/', data={
            'status': 'ERROR', 'error_message': 'Blender exited with code 1',
        }, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert job.status == JobStatus.ERROR

    def test_claim(self, worker_with_token, asset52):
        worker, client = worker_with_token
        Worker.objects.filter(pk=worker.pk).update(
            available_tools={'blender': [version('5.2').resolved_version]},
        )
        job = Job.objects.create(
            name='LegacyQueued', asset=asset52, output_file_pattern='render_####.png',
            start_frame=1, end_frame=1, cycles_feature_set='EXPERIMENTAL',
        )
        resp = client.post(f'{JOBS_URL}{job.pk}/claim/', data={'worker_id': worker.pk}, format='json')
        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'RENDERING'

    def test_admin_patch_of_other_fields(self, admin_client, stored_job):
        job, _ = stored_job
        resp = admin_client.patch(f'{JOBS_URL}{job.pk}/', data={'name': 'Renamed01'}, format='json')
        assert resp.status_code == 200, resp.data
        assert Job.objects.get(pk=job.pk).name == 'Renamed01'


@pytest.fixture
def unreal(db):
    return unreal_asset()


def _unreal_post(client, asset, **extra):
    body = {
        'name': 'UnrealStill', 'asset_id': asset.pk, 'output_file_pattern': '//render/still.png',
        'start_frame': 1000, 'end_frame': 1000, 'render_settings': dict(RENDER_SETTINGS),
    }
    body.update(extra)
    return client.post(JOBS_URL, body, format='json')


@pytest.mark.django_db
class TestUnrealJobs:
    """The #244 rule is Blender's: Unreal keeps its own feature set rule."""

    def test_supported_created(self, admin_client, unreal):
        resp = _unreal_post(admin_client, unreal, cycles_feature_set='SUPPORTED')
        assert resp.status_code == 201, resp.data

    def test_omitted_created(self, admin_client, unreal):
        resp = _unreal_post(admin_client, unreal)
        assert resp.status_code == 201, resp.data
        assert Job.objects.get(pk=resp.data['id']).cycles_feature_set == 'SUPPORTED'

    def test_experimental_refused_by_unreal_rule(self, admin_client, unreal):
        resp = _unreal_post(admin_client, unreal, cycles_feature_set='EXPERIMENTAL')
        assert resp.status_code == 400
        assert resp.data['cycles_feature_set'] == [
            'cycles_feature_set is not applicable to Unreal jobs.'
        ]
        assert not Job.objects.exists()
