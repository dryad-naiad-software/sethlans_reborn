# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Tile jobs through the locked worker write path (issue #217).

The worker PATCH now saves with ``update_fields`` under the job row lock.
The ``post_save`` handler must still see every tile completion and
trigger assembly once, and a tile canceled during its DONE report must
not count as DONE.
"""

import pytest

from workers.models import Job, JobStatus, TiledJob, TiledJobStatus
from workers.views.jobs import JobViewSet

from .worker_write_race_helpers import (
    JOBS_URL, cancel_job, claim, png_upload, run_after,
)

TILED_JOBS_URL = '/api/tiled-jobs/'
ASSEMBLE = 'workers.image_assembler.assemble_tiled_job_image'


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _create_tiled_job(admin_client, project, asset, name):
    resp = admin_client.post(
        TILED_JOBS_URL,
        data={
            'name': name,
            'project': str(project.pk),
            'asset_id': asset.pk,
            'final_resolution_x': 64,
            'final_resolution_y': 32,
            'tile_count_x': 2,
            'tile_count_y': 1,
        },
        format='json',
    )
    assert resp.status_code == 201, resp.data
    tiled = TiledJob.objects.get(name=name)
    return tiled, list(Job.objects.filter(tiled_job=tiled).order_by('name'))


def _claim_and_upload(worker, worker_client, tile):
    claim(worker, worker_client, tile.pk)
    resp = worker_client.post(
        f'{JOBS_URL}{tile.pk}/upload_output/',
        data={'output_file': png_upload(f'{tile.name}.png', (32, 32))},
        format='multipart',
    )
    assert resp.status_code == 200, resp.data


def _report_done(worker_client, tile):
    return worker_client.patch(
        f'{JOBS_URL}{tile.pk}/',
        data={'status': 'DONE', 'render_time_seconds': 3},
        format='json',
    )


def _render_tile(worker, worker_client, tile):
    """Claim, upload and report one tile the way the worker agent does."""
    _claim_and_upload(worker, worker_client, tile)
    return _report_done(worker_client, tile)


@pytest.mark.django_db(transaction=True)
class TestTileAssemblyThroughWorkerPatch:

    def test_last_tile_done_triggers_assembly_once(
        self, mocker, media_root, admin_client, worker_with_token,
        project, asset,
    ):
        worker, worker_client = worker_with_token
        assemble = mocker.patch(ASSEMBLE)
        tiled, tiles = _create_tiled_job(
            admin_client, project, asset, 'TileLock1',
        )

        assert _render_tile(worker, worker_client, tiles[0]).status_code == 200
        assemble.assert_not_called()
        assert _render_tile(worker, worker_client, tiles[1]).status_code == 200

        assemble.assert_called_once_with(tiled.id)
        tiled.refresh_from_db()
        assert tiled.status == TiledJobStatus.ASSEMBLING
        assert tiled.total_render_time_seconds == 6

    def test_tile_canceled_during_done_report_blocks_assembly(
        self, mocker, media_root, admin_client, worker_with_token,
        project, asset,
    ):
        worker, worker_client = worker_with_token
        assemble = mocker.patch(ASSEMBLE)
        tiled, tiles = _create_tiled_job(
            admin_client, project, asset, 'TileLock2',
        )
        assert _render_tile(worker, worker_client, tiles[0]).status_code == 200

        _claim_and_upload(worker, worker_client, tiles[1])

        run_after(
            mocker, (JobViewSet, '_check_worker_owns_job'),
            cancel_job(admin_client, tiles[1].pk),
        )
        resp = _report_done(worker_client, tiles[1])

        assert resp.status_code == 409, resp.data
        assert Job.objects.get(pk=tiles[1].pk).status == JobStatus.CANCELED
        assemble.assert_not_called()
        tiled.refresh_from_db()
        assert tiled.status == TiledJobStatus.ERROR
