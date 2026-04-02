# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for TiledJob creation and tile assembly.

Covers: correct NxN tile job spawning with border settings,
and assembly signal trigger on all tiles completing.
"""

import pytest
from unittest.mock import patch

from workers.models import TiledJob, Job, JobStatus
from workers.constants import RenderSettings

TILED_JOBS_URL = '/api/tiled-jobs/'


@pytest.mark.django_db
class TestTiledJobSpawning:

    def test_create_tiled_job_spawns_nxn_tile_jobs(
        self, admin_client, project, asset,
    ):
        """Creating a 3x3 tiled job spawns 9 tile jobs."""
        resp = admin_client.post(
            TILED_JOBS_URL,
            data={
                'name': 'TileRend001',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'final_resolution_x': 1920,
                'final_resolution_y': 1080,
                'tile_count_x': 3,
                'tile_count_y': 3,
            },
            format='json',
        )
        assert resp.status_code == 201

        tiled_job = TiledJob.objects.get(name='TileRend001')
        tile_jobs = Job.objects.filter(tiled_job=tiled_job)
        assert tile_jobs.count() == 9

    def test_tile_jobs_have_correct_border_settings(
        self, admin_client, project, asset,
    ):
        """Tile jobs contain correct border min/max values."""
        admin_client.post(
            TILED_JOBS_URL,
            data={
                'name': 'BorderTest1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'final_resolution_x': 1920,
                'final_resolution_y': 1080,
                'tile_count_x': 2,
                'tile_count_y': 2,
            },
            format='json',
        )
        tiled_job = TiledJob.objects.get(name='BorderTest1')
        tile_jobs = Job.objects.filter(tiled_job=tiled_job)
        assert tile_jobs.count() == 4

        # Check that border settings use_border is True for all tiles
        for job in tile_jobs:
            rs = job.render_settings
            assert rs[RenderSettings.USE_BORDER] is True
            assert rs[RenderSettings.CROP_TO_BORDER] is True
            assert rs[RenderSettings.RESOLUTION_X] == 1920
            assert rs[RenderSettings.RESOLUTION_Y] == 1080

        # Verify one specific tile's borders: tile_0_0 should be
        # bottom-left corner (0.0 to 0.5)
        tile_0_0 = tile_jobs.get(name='BorderTest1_Tile_0_0')
        rs = tile_0_0.render_settings
        assert rs[RenderSettings.BORDER_MIN_X] == 0.0
        assert rs[RenderSettings.BORDER_MAX_X] == 0.5
        assert rs[RenderSettings.BORDER_MIN_Y] == 0.0
        assert rs[RenderSettings.BORDER_MAX_Y] == 0.5

    def test_create_2x2_tiled_job_spawns_4_jobs(
        self, admin_client, project, asset,
    ):
        """A 2x2 grid spawns exactly 4 tile jobs."""
        resp = admin_client.post(
            TILED_JOBS_URL,
            data={
                'name': 'Small2x2Tj',
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
        tiled_job = TiledJob.objects.get(name='Small2x2Tj')
        assert Job.objects.filter(tiled_job=tiled_job).count() == 4


@pytest.mark.django_db
class TestTiledJobAssembly:

    @patch('workers.image_assembler.assemble_tiled_job_image')
    def test_all_tiles_complete_triggers_assembly(
        self, mock_assemble, admin_client, project, asset,
    ):
        """All tiles completing triggers assembly signal."""
        admin_client.post(
            TILED_JOBS_URL,
            data={
                'name': 'AssemblyTj1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'final_resolution_x': 1920,
                'final_resolution_y': 1080,
                'tile_count_x': 2,
                'tile_count_y': 2,
            },
            format='json',
        )
        tiled_job = TiledJob.objects.get(name='AssemblyTj1')
        tile_jobs = Job.objects.filter(tiled_job=tiled_job)

        # Complete all tiles
        for job in tile_jobs:
            job.status = JobStatus.DONE
            job.render_time_seconds = 5
            job.save()

        mock_assemble.assert_called_once_with(tiled_job.id)

    @patch('workers.image_assembler.assemble_tiled_job_image')
    def test_partial_tiles_do_not_trigger_assembly(
        self, mock_assemble, admin_client, project, asset,
    ):
        """Partial tile completion does not trigger assembly."""
        admin_client.post(
            TILED_JOBS_URL,
            data={
                'name': 'PartialTj1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'final_resolution_x': 1920,
                'final_resolution_y': 1080,
                'tile_count_x': 2,
                'tile_count_y': 2,
            },
            format='json',
        )
        tiled_job = TiledJob.objects.get(name='PartialTj1')
        tile_jobs = list(Job.objects.filter(tiled_job=tiled_job))

        # Complete only first tile
        tile_jobs[0].status = JobStatus.DONE
        tile_jobs[0].render_time_seconds = 5
        tile_jobs[0].save()

        mock_assemble.assert_not_called()

        # TiledJob should be in RENDERING status
        tiled_job.refresh_from_db()
        assert tiled_job.status == JobStatus.RENDERING
