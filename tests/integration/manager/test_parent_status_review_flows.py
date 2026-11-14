# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the #219 concurrency-review fixes to parent status.

* M2: cascade requeue honours a refresh that asks for assembly or video,
  even when nothing was requeued (parent RENDERING, children all DONE).
* L1: the ASSEMBLING claim is a compare-and-set won exactly once.
* An assembly failure is recognised by its reason, not inferred from
  "ERROR + every child DONE".
* L2: project cancel-all refreshes only the parents of the jobs it
  canceled, with the shared rules (no ERROR for an all-DONE or ASSEMBLING
  parent; tiled-animation frames move to ERROR too).
"""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.constants import TilingConfiguration
from workers.models import (
    Animation, AnimationFrame, AnimationFrameStatus, Job, JobStatus,
    TiledJob, TiledJobStatus,
)
from workers.parent_status import _claim_assembly, refresh_tiled_job
from workers.parent_status_rules import assembly_failure_reason

TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'
PROJECTS_URL = '/api/projects/'


def _tiled_job(admin_client, project, asset, name):
    resp = admin_client.post(TILED_JOBS_URL, data={
        'name': name, 'project': str(project.pk), 'asset_id': asset.pk,
        'final_resolution_x': 800, 'final_resolution_y': 600,
        'tile_count_x': 2, 'tile_count_y': 2,
    }, format='json')
    assert resp.status_code == 201
    return TiledJob.objects.get(name=name)


def _tiled_animation(admin_client, project, asset, name):
    resp = admin_client.post(ANIMATIONS_URL, data={
        'name': name, 'project': str(project.pk), 'asset_id': asset.pk,
        'output_file_pattern': '//render/f_####.png',
        'start_frame': 1, 'end_frame': 2, 'frame_step': 1,
        'tiling_config': TilingConfiguration.TILE_2X2,
        'render_settings': {'render.resolution_x': 64, 'render.resolution_y': 64},
    }, format='json')
    assert resp.status_code == 201
    anim = Animation.objects.get(name=name)
    return (anim, *anim.frames.order_by('frame_number'))


def _fake_frame_assembly(frame_id):
    frame = AnimationFrame.objects.get(pk=frame_id)
    frame.status = AnimationFrameStatus.DONE
    frame.save()


@pytest.mark.django_db
class TestCascadeRequeueDispatch:

    @patch('workers.image_assembler.assemble_tiled_job_image')
    def test_requeue_with_all_tiles_done_assembles(
        self, mock_assemble, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        tiled_job = _tiled_job(admin_client, project, asset, 'TjM2Done1')
        Job.objects.filter(tiled_job=tiled_job).update(status=JobStatus.DONE)
        TiledJob.objects.filter(pk=tiled_job.pk).update(status=TiledJobStatus.RENDERING)

        with django_capture_on_commit_callbacks(execute=True):
            resp = admin_client.post(f'{TILED_JOBS_URL}{tiled_job.pk}/requeue/')
        assert resp.data['requeued'] == 0

        mock_assemble.assert_called_once_with(tiled_job.pk)
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ASSEMBLING

    def test_untiled_requeue_with_all_frames_done_completes(
        self, admin_client, project, asset,
    ):
        anim = Animation.objects.create(
            project=project, asset=asset, name='AnM2Done1',
            output_file_pattern='//render/f_####.png', start_frame=1, end_frame=2,
            video_settings={'preset': 'web_h264', 'framerate': 24, 'container': 'mp4',
                            'codec': 'libx264', 'crf': 23},
            video_status='PENDING',
        )
        for n in (1, 2):
            Job.objects.create(
                name=f'AnM2F{n}', asset=asset, animation=anim,
                output_file_pattern='//render/f_####.png',
            )
        Job.objects.filter(animation=anim).update(status=JobStatus.DONE)
        Animation.objects.filter(pk=anim.pk).update(status=JobStatus.RENDERING)

        admin_client.post(f'{ANIMATIONS_URL}{anim.pk}/requeue/')

        anim.refresh_from_db()
        assert anim.status == JobStatus.DONE
        assert anim.video_status == 'ASSEMBLING'  # video trigger honoured

    @patch('workers.image_assembler.assemble_animation_frame_image',
           side_effect=_fake_frame_assembly)
    def test_tiled_animation_requeue_assembles_ready_frames(
        self, _asm, admin_client, project, asset, django_capture_on_commit_callbacks,
    ):
        anim, f1, f2 = _tiled_animation(admin_client, project, asset, 'TaM2Done1')
        Job.objects.filter(animation=anim).update(status=JobStatus.DONE)

        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(f'{ANIMATIONS_URL}{anim.pk}/requeue/')

        assert _asm.call_count == 2
        anim.refresh_from_db()
        assert anim.status == JobStatus.DONE


@pytest.mark.django_db
class TestAssemblyClaim:

    def _all_done(self, admin_client, project, asset, name, **parent_fields):
        tiled_job = _tiled_job(admin_client, project, asset, name)
        Job.objects.filter(tiled_job=tiled_job).update(status=JobStatus.DONE)
        TiledJob.objects.filter(pk=tiled_job.pk).update(**parent_fields)
        return tiled_job

    def test_claim_is_won_once(self, admin_client, project, asset):
        tiled_job = self._all_done(
            admin_client, project, asset, 'TjL1Once', status=TiledJobStatus.RENDERING,
        )
        assert refresh_tiled_job(tiled_job.pk) is True
        assert refresh_tiled_job(tiled_job.pk) is False

    def test_claim_with_stale_status_loses(self, admin_client, project, asset):
        tiled_job = self._all_done(
            admin_client, project, asset, 'TjL1Stale', status=TiledJobStatus.RENDERING,
        )
        stale = TiledJob.objects.get(pk=tiled_job.pk)
        TiledJob.objects.filter(pk=tiled_job.pk).update(status=TiledJobStatus.ASSEMBLING)
        assert _claim_assembly(stale, TiledJobStatus.ASSEMBLING) is False

    def test_error_from_tile_failure_is_claimed_once_tiles_done(
        self, admin_client, project, asset,
    ):
        tiled_job = self._all_done(
            admin_client, project, asset, 'TjMarker1', status=TiledJobStatus.ERROR,
            error_message='1 of 4 tiles did not complete (1 failed).',
        )
        assert refresh_tiled_job(tiled_job.pk) is True

    def test_error_from_assembly_failure_is_never_reclaimed(
        self, admin_client, project, asset,
    ):
        reason = assembly_failure_reason('Tile', ValueError('bad tile'))
        tiled_job = self._all_done(
            admin_client, project, asset, 'TjMarker2',
            status=TiledJobStatus.ERROR, error_message=reason,
        )
        assert refresh_tiled_job(tiled_job.pk) is False
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR
        assert tiled_job.error_message == reason


@pytest.mark.django_db
class TestCancelAllUsesSharedRules:

    def _cancel_all(self, admin_client, project):
        resp = admin_client.post(f'{PROJECTS_URL}{project.pk}/cancel_all_jobs/')
        assert resp.status_code == 200
        return resp.data['canceled']

    def test_cancel_update_keeps_status_predicate_on_job_table(
        self, admin_client, project, asset,
    ):
        """#217 review B: no ``id IN (subquery with status)`` rewrite.

        PostgreSQL rechecks only the target-table predicates after a lock
        wait, so the status filter must be on ``workers_job`` itself.
        """
        Job.objects.create(name='StandaloneB1', asset=asset, output_file_pattern='//r/#.png')
        with CaptureQueriesContext(connection) as ctx:
            assert self._cancel_all(admin_client, project) == 1
        update = next(
            q['sql'] for q in ctx.captured_queries
            if q['sql'].startswith('UPDATE "workers_job"')
        )
        assert '"workers_job"."status" IN' in update
        assert 'FROM "workers_job"' not in update  # no self-subquery

    def test_untouched_all_done_parent_is_not_errored(self, admin_client, project, asset):
        tiled_job = _tiled_job(admin_client, project, asset, 'TjL2Done1')
        Job.objects.filter(tiled_job=tiled_job).update(status=JobStatus.DONE)
        TiledJob.objects.filter(pk=tiled_job.pk).update(status=TiledJobStatus.RENDERING)
        Job.objects.create(name='StandaloneL2', asset=asset, output_file_pattern='//r/#.png')

        assert self._cancel_all(admin_client, project) == 1
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.RENDERING

    def test_assembling_parent_is_left_alone(self, admin_client, project, asset):
        tiled_job = _tiled_job(admin_client, project, asset, 'TjL2Asm01')
        tiles = list(tiled_job.jobs.order_by('name'))
        Job.objects.filter(pk__in=[t.pk for t in tiles[1:]]).update(status=JobStatus.DONE)
        TiledJob.objects.filter(pk=tiled_job.pk).update(status=TiledJobStatus.ASSEMBLING)

        assert self._cancel_all(admin_client, project) == 1
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ASSEMBLING

    def test_canceled_parent_gets_reason(self, admin_client, project, asset):
        tiled_job = _tiled_job(admin_client, project, asset, 'TjL2Canc1')
        assert self._cancel_all(admin_client, project) == 4
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.ERROR
        assert tiled_job.error_message == '4 of 4 tiles did not complete (4 canceled).'

    def test_tiled_animation_frames_and_animation_error(self, admin_client, project, asset):
        anim, f1, f2 = _tiled_animation(admin_client, project, asset, 'TaL2Canc1')
        Job.objects.filter(animation_frame=f1).update(status=JobStatus.DONE)
        AnimationFrame.objects.filter(pk=f1.pk).update(status=AnimationFrameStatus.DONE)

        assert self._cancel_all(admin_client, project) == 4
        f2.refresh_from_db()
        anim.refresh_from_db()
        assert f2.status == AnimationFrameStatus.ERROR
        assert '(4 canceled)' in f2.error_message
        assert anim.status == JobStatus.ERROR
        assert 'Frame 2' in anim.error_message
