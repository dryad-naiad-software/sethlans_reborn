# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for TiledJob status when a tile job fails (issue #219).

Covers: a failed tile keeps the parent RENDERING while siblings still run,
moves it to ERROR with a reason once every tile is terminal, a requeue
(single job or cascade) returns the parent to an active state so the retry
can complete DONE, and an assembly failure stores a reason.
"""

from unittest.mock import patch

import pytest

from workers.models import Job, JobStatus, TiledJob, TiledJobStatus

TILED_JOBS_URL = '/api/tiled-jobs/'
JOBS_URL = '/api/jobs/'


def _create_tiled_job(admin_client, project, asset, name):
    resp = admin_client.post(
        TILED_JOBS_URL,
        data={
            'name': name,
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 800,
            'final_resolution_y': 600,
            'tile_count_x': 2,
            'tile_count_y': 2,
        },
        format='json',
    )
    assert resp.status_code == 201
    tiled_job = TiledJob.objects.get(name=name)
    return tiled_job, list(tiled_job.jobs.order_by('name'))


def _set(job, status, error_message='', render_time=None):
    job.status = status
    job.error_message = error_message
    if render_time is not None:
        job.render_time_seconds = render_time
    job.save()


@pytest.mark.django_db
@patch('workers.image_assembler.assemble_tiled_job_image')
class TestTiledJobTileFailure:

    def test_stays_rendering_while_other_tiles_run(
        self, mock_assemble, admin_client, project, asset,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail001')
        _set(tiles[0], JobStatus.RENDERING)
        _set(tiles[0], JobStatus.ERROR, 'CUDA out of memory')
        _set(tiles[1], JobStatus.DONE, render_time=5)
        _set(tiles[2], JobStatus.RENDERING)

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.RENDERING
        assert tiled_job.error_message == ''
        assert tiled_job.completed_at is None
        mock_assemble.assert_not_called()

    def test_error_once_all_tiles_terminal(
        self, mock_assemble, admin_client, project, asset,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail002')
        _set(tiles[0], JobStatus.ERROR, 'CUDA out of memory')
        for tile in tiles[1:]:
            _set(tile, JobStatus.DONE, render_time=5)

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR
        assert tiled_job.completed_at is not None
        assert '1 of 4 tiles did not complete (1 failed)' in tiled_job.error_message
        assert tiles[0].name in tiled_job.error_message
        assert 'CUDA out of memory' in tiled_job.error_message
        assert tiled_job.total_render_time_seconds == 15
        mock_assemble.assert_not_called()

    def test_canceled_tile_sets_error_with_reason(
        self, mock_assemble, admin_client, project, asset,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail003')
        _set(tiles[0], JobStatus.CANCELED)
        for tile in tiles[1:]:
            _set(tile, JobStatus.DONE)

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR
        assert '(1 canceled)' in tiled_job.error_message

    def test_error_reason_returned_by_api(
        self, mock_assemble, admin_client, project, asset,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail004')
        for tile in tiles:
            _set(tile, JobStatus.ERROR, 'Blender crashed')

        resp = admin_client.get(f'{TILED_JOBS_URL}{tiled_job.pk}/')
        assert resp.status_code == 200
        assert resp.data['status'] == TiledJobStatus.ERROR
        assert '4 of 4 tiles did not complete' in resp.data['error_message']

    def test_single_job_requeue_returns_parent_to_active_then_done(
        self, mock_assemble, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail005')
        _set(tiles[0], JobStatus.ERROR, 'boom')
        for tile in tiles[1:]:
            _set(tile, JobStatus.DONE)
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR

        resp = admin_client.post(f'{JOBS_URL}{tiles[0].pk}/requeue/')
        assert resp.status_code == 200

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.RENDERING
        assert tiled_job.error_message == ''
        assert tiled_job.completed_at is None

        retry = Job.objects.get(pk=tiles[0].pk)
        with django_capture_on_commit_callbacks(execute=True):
            _set(retry, JobStatus.RENDERING)
            _set(retry, JobStatus.DONE)
        mock_assemble.assert_called_once_with(tiled_job.id)
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ASSEMBLING

    def test_cascade_requeue_returns_parent_to_active_then_done(
        self, mock_assemble, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail006')
        for tile in tiles:
            _set(tile, JobStatus.ERROR, 'boom')
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR

        resp = admin_client.post(f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/')
        assert resp.data['requeued'] == 4

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.QUEUED
        assert tiled_job.error_message == ''
        assert tiled_job.completed_at is None

        with django_capture_on_commit_callbacks(execute=True):
            for tile in Job.objects.filter(tiled_job=tiled_job):
                _set(tile, JobStatus.RENDERING)
                _set(tile, JobStatus.DONE)
        mock_assemble.assert_called_once_with(tiled_job.id)

    def test_all_tiles_done_unchanged(
        self, mock_assemble, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        """All-success path: no ERROR, render time summed, assembly once."""
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjFail007')
        with django_capture_on_commit_callbacks(execute=True):
            for tile in tiles:
                _set(tile, JobStatus.RENDERING)
                tiled_job.refresh_from_db()
                assert tiled_job.status == TiledJobStatus.RENDERING
                _set(tile, JobStatus.DONE, render_time=3)
            # M1: assembly is deferred until the transaction commits.
            mock_assemble.assert_not_called()

        mock_assemble.assert_called_once_with(tiled_job.id)
        tiled_job.refresh_from_db()
        assert tiled_job.total_render_time_seconds == 12
        assert tiled_job.error_message == ''


@pytest.mark.django_db
class TestTiledJobAssemblyFailure:

    def test_assembly_failure_stores_reason(
        self, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        """Real assembler: DONE tiles without output files cannot be
        stitched, so the TiledJob goes to ERROR with a stored reason."""
        tiled_job, tiles = _create_tiled_job(admin_client, project, asset, 'TjAsmFail1')
        with django_capture_on_commit_callbacks(execute=True):
            for tile in tiles:
                _set(tile, JobStatus.DONE)

        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR
        assert tiled_job.error_message.startswith('Tile assembly failed: ')
        assert tiled_job.completed_at is not None

        resp = admin_client.get(f'{TILED_JOBS_URL}{tiled_job.pk}/')
        assert resp.data['error_message'] == tiled_job.error_message
