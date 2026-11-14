# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a worker poll returns jobs oldest first (spec D7, FR-RT-15).

``apply_worker_poll_filters`` orders every worker poll by
``(submitted_at, id)``.  Non-poll lists keep ``Job.Meta.ordering``
(newest first), an explicit ``ordering=`` still wins, and requeued jobs
keep their ``submitted_at`` and so return to the front (accepted, D10g).
"""

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from workers.models import Asset, Job, JobStatus, Project, TiledJob

from .unreal_helpers import BUILD, SHARE, caps, set_share

JOBS_URL = '/api/jobs/'
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}
BASE = datetime(2025, 3, 1, tzinfo=dt_timezone.utc)


def _job(asset, name, minutes, **extra):
    """ORM job pinned to ``BASE + minutes``."""
    job = Job.objects.create(
        name=name, asset=asset, output_file_pattern=f'//render/{name}_####.png',
        **extra,
    )
    Job.objects.filter(pk=job.pk).update(submitted_at=BASE + timedelta(minutes=minutes))
    return job


def _names(resp):
    assert resp.status_code == 200, resp.data
    return [job['name'] for job in resp.data]


def _poll(client, **params):
    return client.get(JOBS_URL, {**POLL, **params})


@pytest.fixture
def unreal_asset(db):
    """An Unreal project and shared-project asset, created via the ORM."""
    project = Project.objects.create(
        name='FifoUnreal', renderer='UNREAL', engine_build='5.8.4-58902052',
    )
    return Asset.objects.create(
        name='FifoUnrealAsset', project=project, source_type='shared_project',
        archive_path='incoming/scene.zip', uproject_path='Scene/Scene.uproject',
        archive_sha256='a' * 64,
    )


@pytest.mark.django_db
class TestOldestFirst:

    def test_poll_returns_oldest_first_and_claim_takes_oldest(self, worker_with_token, asset):
        worker, client = worker_with_token
        _job(asset, 'FifoMiddle', 2)
        _job(asset, 'FifoOldest', 1)
        _job(asset, 'FifoNewest', 3)

        names = _names(_poll(client))
        assert names == ['FifoOldest', 'FifoMiddle', 'FifoNewest']

        # The worker claims the first job in response order.
        first = _poll(client).data[0]
        claim = client.post(f"{JOBS_URL}{first['id']}/claim/", {'worker_id': worker.pk}, format='json')
        assert claim.status_code == 200, claim.data
        assert Job.objects.get(status=JobStatus.RENDERING).name == 'FifoOldest'

    def test_identical_submitted_at_ties_break_by_id(self, worker_with_token, asset):
        _worker, client = worker_with_token
        first = _job(asset, 'FifoTieA', 5)
        second = _job(asset, 'FifoTieB', 5)
        assert first.pk < second.pk
        assert _names(_poll(client)) == ['FifoTieA', 'FifoTieB']

    def test_bulk_created_animation_children_in_id_order(self, admin_client, worker_with_token, project, asset):
        _worker, client = worker_with_token
        resp = admin_client.post('/api/animations/', {
            'name': 'FifoAnim', 'project': str(project.pk), 'asset_id': asset.pk,
            'output_file_pattern': '//render/anim/frame_####.png',
            'start_frame': 1, 'end_frame': 4, 'frame_step': 1,
        }, format='json')
        assert resp.status_code == 201, resp.data
        children = Job.objects.filter(animation_id=resp.data['id'])
        # Force a tie: bulk-created children may differ by microseconds only.
        children.update(submitted_at=BASE)
        expected = list(children.order_by('id').values_list('name', flat=True))
        assert expected == [f'FifoAnim_Frame_{n:04d}' for n in range(1, 5)]
        assert _names(_poll(client)) == expected

    def test_non_poll_list_is_still_newest_first(self, admin_client, asset):
        _job(asset, 'FifoListOld', 1)
        _job(asset, 'FifoListNew', 2)
        assert _names(admin_client.get(JOBS_URL)) == ['FifoListNew', 'FifoListOld']

    def test_real_worker_params_keep_the_order(self, worker_with_token, asset, default_version):
        _worker, client = worker_with_token
        _job(asset, 'FifoParamsB', 2, render_device='CPU')
        _job(asset, 'FifoParamsA', 1, render_device='GPU')
        _job(asset, 'FifoParamsC', 3)
        names = _names(_poll(
            client, device_prefs='GPU,CPU,ANY',
            available_versions=default_version.resolved_version,
        ))
        assert names == ['FifoParamsA', 'FifoParamsB', 'FifoParamsC']

    def test_explicit_ordering_on_a_poll_still_wins(self, worker_with_token, asset):
        _worker, client = worker_with_token
        _job(asset, 'FifoOrderOld', 1)
        _job(asset, 'FifoOrderNew', 2)
        names = _names(_poll(client, ordering='-submitted_at'))
        assert names == ['FifoOrderNew', 'FifoOrderOld']

    def test_yield_requeued_job_is_first_in_next_poll(self, worker_with_token, asset):
        worker, client = worker_with_token
        _job(asset, 'FifoYieldOld', 1)
        _job(asset, 'FifoYieldNew', 2)
        old = Job.objects.get(name='FifoYieldOld')
        assert client.post(f'{JOBS_URL}{old.pk}/claim/', {'worker_id': worker.pk}, format='json').status_code == 200
        assert _names(_poll(client)) == ['FifoYieldNew']

        resp = client.post(
            f'{JOBS_URL}{old.pk}/yield-requeue/', {'reason': 'manual_override'}, format='json',
        )
        assert resp.status_code == 200, resp.data
        assert _names(_poll(client)) == ['FifoYieldOld', 'FifoYieldNew']


@pytest.mark.django_db
class TestSpawnedTileOrder:

    def test_tiled_job_tiles_in_row_major_order(self, admin_client, worker_with_token, project, asset):
        _worker, client = worker_with_token
        resp = admin_client.post('/api/tiled-jobs/', {
            'name': 'FifoTiled', 'project': str(project.pk), 'asset_id': asset.pk,
            'final_resolution_x': 640, 'final_resolution_y': 480,
            'tile_count_x': 2, 'tile_count_y': 2,
        }, format='json')
        assert resp.status_code == 201, resp.data
        Job.objects.filter(tiled_job_id=resp.data['id']).update(submitted_at=BASE)
        expected = [f'FifoTiled_Tile_{y}_{x}' for y in range(2) for x in range(2)]
        assert _names(_poll(client)) == expected

    def test_tiled_animation_is_frame_major_then_row_major(self, admin_client, worker_with_token, project, asset):
        _worker, client = worker_with_token
        resp = admin_client.post('/api/animations/', {
            'name': 'FifoTAnim', 'project': str(project.pk), 'asset_id': asset.pk,
            'output_file_pattern': '//render/tanim/frame_####.png',
            'start_frame': 1, 'end_frame': 2, 'tiling_config': '2x2',
            'render_settings': {'render.resolution_x': 640, 'render.resolution_y': 480},
        }, format='json')
        assert resp.status_code == 201, resp.data
        Job.objects.filter(animation_id=resp.data['id']).update(submitted_at=BASE)
        expected = [
            f'FifoTAnim_Frame_{f:04d}_Tile_{y}_{x}'
            for f in (1, 2) for y in range(2) for x in range(2)
        ]
        assert _names(_poll(client)) == expected


@pytest.mark.django_db
class TestRendererInterleaving:
    """No special treatment of Unreal jobs in the queue (D1)."""

    def _jobs(self, asset, unreal_asset):
        _job(asset, 'MixBlender1', 1)
        _job(unreal_asset, 'MixUnreal2', 2)
        _job(asset, 'MixBlender3', 3)
        _job(unreal_asset, 'MixUnreal4', 4)
        _job(unreal_asset, 'MixUnreal4b', 4)
        _job(asset, 'MixBlender4c', 4)

    def test_renderers_interleave_strictly_by_submitted_at_then_id(
        self, worker_with_token, asset, unreal_asset,
    ):
        """With Unreal routing in place, order ignores the renderer."""
        worker, client = worker_with_token
        set_share(SHARE)
        worker.unreal_capabilities = caps()
        worker.save(update_fields=['unreal_capabilities'])
        self._jobs(asset, unreal_asset)
        names = _names(_poll(client, renderers='BLENDER,UNREAL', unreal_builds=BUILD))
        assert names == [
            'MixBlender1', 'MixUnreal2', 'MixBlender3',
            'MixUnreal4', 'MixUnreal4b', 'MixBlender4c',
        ]

    def test_excluded_unreal_jobs_keep_blender_order(self, worker_with_token, asset, unreal_asset):
        """Share unset: no Unreal job is offered and the Blender order holds."""
        _worker, client = worker_with_token
        self._jobs(asset, unreal_asset)
        names = _names(_poll(client, renderers='BLENDER,UNREAL', unreal_builds=BUILD))
        assert names == ['MixBlender1', 'MixBlender3', 'MixBlender4c']

    def test_job_meta_ordering_unchanged(self):
        assert Job._meta.ordering == ['-submitted_at']
        assert TiledJob._meta.ordering == ['-submitted_at']
