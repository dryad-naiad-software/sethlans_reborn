# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for Animation status when a child job fails (issue #219).

Untiled animations: a failed frame job keeps the animation active while
other frames run, moves it to ERROR (never DONE, no video assembly) once
every frame job is terminal, and a requeue lets the retry complete DONE.

Tiled animations: a failed tile moves its AnimationFrame to ERROR, the
animation goes to ERROR once every frame is DONE/ERROR, a requeue returns
frame and animation to an active state, and a frame assembly failure is
stored as a reason.
"""

from unittest.mock import patch

import pytest

from workers.constants import TilingConfiguration
from workers.models import (
    Animation, AnimationFrame, AnimationFrameStatus, Job, JobStatus,
)

ANIMATIONS_URL = '/api/animations/'
JOBS_URL = '/api/jobs/'
VIDEO_SETTINGS = {
    'preset': 'web_h264', 'framerate': 24, 'container': 'mp4',
    'codec': 'libx264', 'crf': 23,
}


def _untiled(project, asset, name, frames=3):
    """Animation with video settings and one QUEUED job per frame (ORM)."""
    anim = Animation.objects.create(
        project=project, asset=asset, name=name,
        output_file_pattern='//render/f_####.png',
        start_frame=1, end_frame=frames,
        video_settings=VIDEO_SETTINGS, video_status='PENDING',
    )
    jobs = [
        Job.objects.create(
            name=f'{name}_F{n}', asset=asset, animation=anim,
            output_file_pattern='//render/f_####.png',
            start_frame=n, end_frame=n,
        )
        for n in range(1, frames + 1)
    ]
    return anim, jobs


def _tiled(admin_client, project, asset, name):
    """2-frame, 2x2-tiled animation created via the API."""
    resp = admin_client.post(
        ANIMATIONS_URL,
        data={
            'name': name, 'project': str(project.pk), 'asset_id': asset.pk,
            'output_file_pattern': '//render/f_####.png',
            'start_frame': 1, 'end_frame': 2, 'frame_step': 1,
            'tiling_config': TilingConfiguration.TILE_2X2,
            'render_settings': {
                'render.resolution_x': 64, 'render.resolution_y': 64,
            },
        },
        format='json',
    )
    assert resp.status_code == 201
    anim = Animation.objects.get(name=name)
    f1, f2 = anim.frames.order_by('frame_number')
    return anim, f1, f2


def _tiles(frame):
    return list(Job.objects.filter(animation_frame=frame).order_by('name'))


def _set(job, status, error_message=''):
    job.status = status
    job.error_message = error_message
    job.save()


def _fake_frame_assembly(frame_id):
    """Stand-in for the Pillow assembler: mark the frame DONE via save()."""
    frame = AnimationFrame.objects.get(pk=frame_id)
    frame.status = AnimationFrameStatus.DONE
    frame.save()


@pytest.mark.django_db
class TestUntiledAnimationFrameFailure:

    def test_failed_frame_while_others_run_is_not_done(self, project, asset):
        anim, jobs = _untiled(project, asset, 'UnFail001')
        _set(jobs[0], JobStatus.ERROR, 'segfault')
        _set(jobs[1], JobStatus.DONE)

        anim.refresh_from_db()
        assert anim.status == JobStatus.RENDERING
        assert anim.error_message == ''

    def test_error_once_all_terminal_and_no_video(self, project, asset):
        anim, jobs = _untiled(project, asset, 'UnFail002')
        _set(jobs[0], JobStatus.DONE)
        _set(jobs[1], JobStatus.ERROR, 'segfault in frame 2')
        _set(jobs[2], JobStatus.DONE)

        anim.refresh_from_db()
        assert anim.status == JobStatus.ERROR
        assert anim.completed_at is not None
        assert '1 of 3 frames did not complete (1 failed)' in anim.error_message
        assert 'segfault in frame 2' in anim.error_message
        # Partial success never assembles a video with missing frames.
        assert anim.video_status == 'PENDING'

    def test_requeue_failed_frame_then_done(self, admin_client, project, asset):
        anim, jobs = _untiled(project, asset, 'UnFail003')
        _set(jobs[0], JobStatus.DONE)
        _set(jobs[1], JobStatus.ERROR, 'segfault')
        _set(jobs[2], JobStatus.DONE)

        resp = admin_client.post(f'{JOBS_URL}{jobs[1].pk}/requeue/')
        assert resp.status_code == 200
        anim.refresh_from_db()
        assert anim.status == JobStatus.RENDERING
        assert anim.error_message == ''
        assert anim.completed_at is None

        retry = Job.objects.get(pk=jobs[1].pk)
        _set(retry, JobStatus.RENDERING)
        _set(retry, JobStatus.DONE)
        anim.refresh_from_db()
        assert anim.status == JobStatus.DONE
        assert anim.video_status == 'ASSEMBLING'

    def test_cascade_requeue_returns_animation_to_active(
        self, admin_client, project, asset,
    ):
        anim, jobs = _untiled(project, asset, 'UnFail004')
        for job in jobs:
            _set(job, JobStatus.ERROR, 'segfault')
        anim.refresh_from_db()
        assert anim.status == JobStatus.ERROR

        resp = admin_client.post(f'{ANIMATIONS_URL}{anim.pk}/requeue/')
        assert resp.data['requeued'] == 3
        anim.refresh_from_db()
        assert anim.status == JobStatus.QUEUED
        assert anim.error_message == ''

    def test_all_frames_done_unchanged(self, project, asset):
        anim, jobs = _untiled(project, asset, 'UnFail005')
        for n, job in enumerate(jobs, start=1):
            job.render_time_seconds = n
            _set(job, JobStatus.DONE)
        anim.refresh_from_db()
        assert anim.status == JobStatus.DONE
        assert anim.total_render_time_seconds == 6
        assert anim.error_message == ''
        assert anim.video_status == 'ASSEMBLING'


@pytest.mark.django_db
@patch(
    'workers.image_assembler.assemble_animation_frame_image',
    side_effect=_fake_frame_assembly,
)
class TestTiledAnimationTileFailure:

    def test_failed_tile_errors_frame_animation_stays_active(
        self, _asm, admin_client, project, asset,
    ):
        anim, f1, f2 = _tiled(admin_client, project, asset, 'TaFail001')
        tiles = _tiles(f2)
        _set(tiles[0], JobStatus.ERROR, 'GPU lost')
        for tile in tiles[1:]:
            _set(tile, JobStatus.DONE)

        f2.refresh_from_db()
        anim.refresh_from_db()
        assert f2.status == AnimationFrameStatus.ERROR
        assert 'GPU lost' in f2.error_message
        assert anim.status == JobStatus.RENDERING  # frame 1 still queued

    def test_animation_error_once_all_frames_terminal(
        self, _asm, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        anim, f1, f2 = _tiled(admin_client, project, asset, 'TaFail002')
        with django_capture_on_commit_callbacks(execute=True):
            for tile in _tiles(f1):
                _set(tile, JobStatus.DONE)
            tiles = _tiles(f2)
            _set(tiles[0], JobStatus.ERROR, 'GPU lost')
            for tile in tiles[1:]:
                _set(tile, JobStatus.DONE)

        anim.refresh_from_db()
        assert anim.status == JobStatus.ERROR
        assert anim.completed_at is not None
        assert '1 of 2 frames did not complete (1 failed)' in anim.error_message
        assert 'Frame 2' in anim.error_message
        assert 'GPU lost' in anim.error_message

    def test_requeue_failed_tile_then_done(
        self, _asm, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        anim, f1, f2 = _tiled(admin_client, project, asset, 'TaFail003')
        with django_capture_on_commit_callbacks(execute=True):
            for tile in _tiles(f1):
                _set(tile, JobStatus.DONE)
            tiles = _tiles(f2)
            _set(tiles[0], JobStatus.ERROR, 'GPU lost')
            for tile in tiles[1:]:
                _set(tile, JobStatus.DONE)

        resp = admin_client.post(f'{ANIMATIONS_URL}{anim.pk}/requeue/')
        assert resp.data['requeued'] == 1
        f2.refresh_from_db()
        anim.refresh_from_db()
        assert f2.status == AnimationFrameStatus.PENDING
        assert f2.error_message == ''
        assert anim.status == JobStatus.RENDERING
        assert anim.error_message == ''

        retry = Job.objects.get(pk=tiles[0].pk)
        with django_capture_on_commit_callbacks(execute=True):
            _set(retry, JobStatus.RENDERING)
            _set(retry, JobStatus.DONE)
        f2.refresh_from_db()
        anim.refresh_from_db()
        assert f2.status == AnimationFrameStatus.DONE
        assert anim.status == JobStatus.DONE

    def test_all_tiles_done_unchanged(
        self, _asm, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        anim, f1, f2 = _tiled(admin_client, project, asset, 'TaFail004')
        with django_capture_on_commit_callbacks(execute=True):
            for frame in (f1, f2):
                for tile in _tiles(frame):
                    _set(tile, JobStatus.DONE)
        assert _asm.call_count == 2
        anim.refresh_from_db()
        assert anim.status == JobStatus.DONE
        assert anim.error_message == ''


@pytest.mark.django_db
class TestTiledAnimationFrameAssemblyFailure:

    def test_frame_assembly_failure_stores_reason(
        self, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        """Real assembler: DONE tiles without output files fail assembly."""
        anim, f1, f2 = _tiled(admin_client, project, asset, 'TaAsmFail1')
        with django_capture_on_commit_callbacks(execute=True):
            for frame in (f1, f2):
                for tile in _tiles(frame):
                    _set(tile, JobStatus.DONE)

        f1.refresh_from_db()
        anim.refresh_from_db()
        assert f1.status == AnimationFrameStatus.ERROR
        assert f1.error_message.startswith('Frame assembly failed: ')
        assert anim.status == JobStatus.ERROR
        assert '2 of 2 frames did not complete' in anim.error_message

        resp = admin_client.get(f'{ANIMATIONS_URL}{anim.pk}/')
        assert resp.data['error_message'] == anim.error_message
        assert resp.data['frames'][0]['error_message'] == f1.error_message
