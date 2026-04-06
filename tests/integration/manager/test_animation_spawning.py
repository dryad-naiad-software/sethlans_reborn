# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for Animation creation and job spawning.

Covers: correct child job count from start/end/step,
completion propagation to parent animation, and render
time aggregation.
"""

import pytest

from workers.constants import TilingConfiguration
from workers.models import Animation, Job, JobStatus

ANIMATIONS_URL = '/api/animations/'


@pytest.mark.django_db
class TestAnimationSpawning:

    def test_create_animation_spawns_correct_frame_jobs(
        self, admin_client, project, asset,
    ):
        """Creating an animation spawns correct number of frame jobs."""
        resp = admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'WalkCycle001',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/frame_####.png',
                'start_frame': 1,
                'end_frame': 10,
                'frame_step': 2,
            },
            format='json',
        )
        assert resp.status_code == 201

        animation = Animation.objects.get(name='WalkCycle001')
        # Frames: 1, 3, 5, 7, 9 = 5 jobs
        jobs = Job.objects.filter(animation=animation)
        assert jobs.count() == 5

        frame_numbers = sorted(
            jobs.values_list('start_frame', flat=True)
        )
        assert frame_numbers == [1, 3, 5, 7, 9]

    def test_create_animation_step_one(
        self, admin_client, project, asset,
    ):
        """Frame step of 1 creates one job per frame."""
        resp = admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'EveryFrame1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/f_####.png',
                'start_frame': 1,
                'end_frame': 5,
                'frame_step': 1,
            },
            format='json',
        )
        assert resp.status_code == 201
        animation = Animation.objects.get(name='EveryFrame1')
        assert Job.objects.filter(animation=animation).count() == 5


@pytest.mark.django_db
class TestAnimationCompletion:

    def test_all_frames_done_marks_animation_done(
        self, admin_client, project, asset,
    ):
        """All frame jobs completing marks animation DONE."""
        admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'CompAnim001',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/f_####.png',
                'start_frame': 1,
                'end_frame': 3,
                'frame_step': 1,
            },
            format='json',
        )
        animation = Animation.objects.get(name='CompAnim001')
        jobs = Job.objects.filter(animation=animation)

        # Complete each job with render time
        for i, job in enumerate(jobs, start=1):
            job.status = JobStatus.DONE
            job.render_time_seconds = 10 * i
            job.save()

        animation.refresh_from_db()
        assert animation.status == JobStatus.DONE
        assert animation.completed_at is not None

    def test_animation_render_time_aggregated(
        self, admin_client, project, asset,
    ):
        """Animation total_render_time_seconds sums child job times."""
        admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'TimeAgg001',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/f_####.png',
                'start_frame': 1,
                'end_frame': 3,
                'frame_step': 1,
            },
            format='json',
        )
        animation = Animation.objects.get(name='TimeAgg001')
        jobs = list(Job.objects.filter(animation=animation))

        render_times = [15, 25, 35]
        for job, rt in zip(jobs, render_times):
            job.status = JobStatus.DONE
            job.render_time_seconds = rt
            job.save()

        animation.refresh_from_db()
        assert animation.total_render_time_seconds == sum(render_times)

    def test_partial_completion_keeps_rendering_status(
        self, admin_client, project, asset,
    ):
        """Partial job completion transitions animation to RENDERING."""
        admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'PartialAn1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/f_####.png',
                'start_frame': 1,
                'end_frame': 3,
                'frame_step': 1,
            },
            format='json',
        )
        animation = Animation.objects.get(name='PartialAn1')
        first_job = Job.objects.filter(animation=animation).first()

        first_job.status = JobStatus.DONE
        first_job.render_time_seconds = 10
        first_job.save()

        animation.refresh_from_db()
        assert animation.status == JobStatus.RENDERING


@pytest.mark.django_db
class TestTiledAnimationOutputPattern:

    def test_tiled_animation_tile_jobs_have_png_extension(
        self, admin_client, project, asset,
    ):
        """Tiled animation tile jobs must have .png extension in output_file_pattern."""
        resp = admin_client.post(
            ANIMATIONS_URL,
            data={
                'name': 'TiledAnim1',
                'project': str(project.pk),
                'asset_id': asset.pk,
                'output_file_pattern': '//render/frame_####.png',
                'start_frame': 1,
                'end_frame': 2,
                'frame_step': 1,
                'tiling_config': TilingConfiguration.TILE_2X2,
                'render_settings': {
                    'render.resolution_x': 1920,
                    'render.resolution_y': 1080,
                },
            },
            format='json',
        )
        assert resp.status_code == 201

        animation = Animation.objects.get(name='TiledAnim1')
        tile_jobs = Job.objects.filter(
            animation=animation, animation_frame__isnull=False,
        )
        assert tile_jobs.count() > 0

        for job in tile_jobs:
            assert job.output_file_pattern.endswith('.png'), (
                f"Tiled animation tile job '{job.name}' output_file_pattern "
                f"'{job.output_file_pattern}' missing .png extension"
            )
