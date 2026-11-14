# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for parent-status dispatch and assembly failure reasons (#219).

``parent_dispatch.dispatch_for_job`` must recompute the parent on every
child save (not only DONE saves), honour every True return of a refresh
(deferred assembly or video trigger), and the image assemblers must store a
reason when assembly raises.  All ORM calls are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from workers import parent_dispatch as dispatch
from workers.models import AnimationFrameStatus, JobStatus, TiledJobStatus


def _job(status, tiled_job_id=None, animation_id=None, frame_id=None, tiling='NONE'):
    job = MagicMock()
    job.status = status
    job.tiled_job_id = tiled_job_id
    job.animation_id = animation_id
    job.animation_frame_id = frame_id
    job.animation.tiling_config = tiling
    return job


@pytest.fixture
def on_commit():
    """Capture ``transaction.on_commit`` callbacks instead of running them."""
    callbacks = []
    with patch.object(dispatch.transaction, 'on_commit',
                      side_effect=lambda cb, **_kw: callbacks.append(cb)):
        yield callbacks


class TestDispatchForJob:

    @pytest.mark.parametrize('status', [JobStatus.ERROR, JobStatus.CANCELED, JobStatus.QUEUED])
    @patch('workers.image_assembler.assemble_tiled_job_image')
    @patch.object(dispatch, 'refresh_tiled_job', return_value=False)
    def test_tile_non_done_save_refreshes_tiled_job(self, refresh, assemble, status, on_commit):
        dispatch.dispatch_for_job(_job(status, tiled_job_id='tj'))
        refresh.assert_called_once_with('tj')
        assert on_commit == []
        assemble.assert_not_called()

    @patch('workers.image_assembler.assemble_tiled_job_image')
    @patch.object(dispatch, 'refresh_tiled_job', return_value=True)
    def test_claimed_assembly_is_deferred_to_commit(self, refresh, assemble, on_commit):
        dispatch.dispatch_for_job(_job(JobStatus.DONE, tiled_job_id='tj'))
        assemble.assert_not_called()  # M1: not inside the caller's transaction
        assert len(on_commit) == 1
        on_commit[0]()
        assemble.assert_called_once_with('tj')

    @patch.object(dispatch, '_trigger_video_for')
    @patch.object(dispatch, 'refresh_untiled_animation', return_value=False)
    def test_failed_untiled_frame_does_not_trigger_video(self, refresh, video):
        dispatch.dispatch_for_job(_job(JobStatus.ERROR, animation_id=7))
        refresh.assert_called_once_with(7)
        video.assert_not_called()

    @patch.object(dispatch, '_trigger_video_for')
    @patch.object(dispatch, 'refresh_untiled_animation', return_value=True)
    def test_completed_untiled_animation_triggers_video(self, refresh, video):
        dispatch.dispatch_for_job(_job(JobStatus.DONE, animation_id=7))
        video.assert_called_once_with(7)

    @patch('workers.image_assembler.assemble_animation_frame_image')
    @patch.object(dispatch, 'refresh_tiled_animation', return_value=False)
    @patch.object(dispatch, 'refresh_animation_frame', return_value=False)
    @patch.object(dispatch, 'AnimationFrame')
    def test_failed_tile_refreshes_frame_then_animation(
        self, frame_model, refresh_frame, refresh_anim, assemble, on_commit,
    ):
        frame_model.objects.filter.return_value.values_list.return_value.first.return_value = 9
        dispatch.dispatch_for_job(
            _job(JobStatus.ERROR, animation_id=9, frame_id=3, tiling='2x2'),
        )
        refresh_frame.assert_called_once_with(3)
        refresh_anim.assert_called_once_with(9)
        assert on_commit == []

    @patch('workers.image_assembler.assemble_animation_frame_image')
    @patch.object(dispatch, 'refresh_tiled_animation')
    @patch.object(dispatch, 'refresh_animation_frame', return_value=True)
    def test_claimed_frame_assembly_is_deferred(
        self, refresh_frame, refresh_anim, assemble, on_commit,
    ):
        dispatch.dispatch_for_job(
            _job(JobStatus.DONE, animation_id=9, frame_id=3, tiling='2x2'),
        )
        refresh_anim.assert_not_called()  # the assembler's saves do that
        on_commit[0]()
        assemble.assert_called_once_with(3)


class TestDispatchParents:

    @patch.object(dispatch, 'dispatch_animation_status')
    @patch.object(dispatch, 'dispatch_frame_only')
    @patch.object(dispatch, 'dispatch_tiled_job')
    def test_each_parent_once_frames_before_animations(self, tiled, frame, anim):
        order = []
        tiled.side_effect = lambda pk: order.append(('tiled', pk))
        frame.side_effect = lambda pk: order.append(('frame', pk))
        anim.side_effect = lambda pk: order.append(('anim', pk))

        dispatch.dispatch_parents([
            ('t2', None, None), ('t1', None, None), ('t1', None, None),
            (None, 5, 12), (None, 5, 11), (None, 4, None),
        ])

        assert order == [
            ('tiled', 't1'), ('tiled', 't2'),
            ('frame', 11), ('frame', 12),
            ('anim', 4), ('anim', 5),
        ]


class TestAssemblyFailureStoresReason:

    @patch('workers.image_assembler.timezone')
    @patch('workers.image_assembler.Image')
    @patch('workers.image_assembler.TiledJob')
    def test_tiled_job_reason(self, tiled_job_model, pil, _tz):
        from workers.image_assembler import assemble_tiled_job_image

        tj = MagicMock(
            render_settings={}, final_resolution_x=10, final_resolution_y=10,
            tile_count_x=2, tile_count_y=2,
        )
        tiled_job_model.objects.get.return_value = tj
        pil.new.side_effect = RuntimeError('image too large')

        assemble_tiled_job_image('tj')

        assert tj.status == TiledJobStatus.ERROR
        assert tj.error_message == 'Tile assembly failed: image too large'
        tj.save.assert_called_with(
            update_fields=['status', 'error_message', 'completed_at'],
        )

    @patch('workers.image_assembler.AnimationFrame')
    def test_animation_frame_reason(self, frame_model):
        from workers.image_assembler import assemble_animation_frame_image

        frame = MagicMock()
        frame.tile_jobs.filter.return_value.order_by.return_value.first.return_value = None
        frame_model.objects.select_related.return_value.get.return_value = frame

        assemble_animation_frame_image(1)

        assert frame.status == AnimationFrameStatus.ERROR
        assert frame.error_message.startswith('Frame assembly failed: ')
        assert 'render settings' in frame.error_message
        frame.save.assert_called_with(update_fields=['status', 'error_message'])
