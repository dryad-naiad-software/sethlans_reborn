# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for parent-status locking and assembler resilience (#219).

* H1: every ``refresh_*`` issues a write as its first statement, so on
  SQLite the transaction takes the write lock before reading (no
  SQLITE_BUSY_SNAPSHOT); see ``tests/unit/manager/test_sqlite_write_first_lock.py``
  for the two-connection reproduction of why this matters.
* H1: a parent refresh that fails during the assembler's DONE save is
  logged; the assembled frame stays DONE instead of flipping to ERROR.
* L3: a refresh that changes nothing does not rewrite the parent row.
"""

import io
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from PIL import Image

from workers import image_assembler
from workers.constants import TilingConfiguration
from workers.models import (
    Animation, AnimationFrameStatus, Job, JobStatus, TiledJob, TiledJobStatus,
)
from workers.parent_status import (
    refresh_animation_frame, refresh_tiled_animation, refresh_tiled_job,
    refresh_untiled_animation,
)

LOCK_SENTINEL = '__write_lock__'


def _statements(ctx):
    return [
        q['sql'] for q in ctx.captured_queries
        if not q['sql'].upper().startswith(('SAVEPOINT', 'RELEASE SAVEPOINT'))
    ]


def _tiled_animation(admin_client, project, asset, name):
    resp = admin_client.post('/api/animations/', data={
        'name': name, 'project': str(project.pk), 'asset_id': asset.pk,
        'output_file_pattern': '//render/f_####.png',
        'start_frame': 1, 'end_frame': 2, 'frame_step': 1,
        'tiling_config': TilingConfiguration.TILE_2X2,
        'render_settings': {'render.resolution_x': 64, 'render.resolution_y': 64},
    }, format='json')
    assert resp.status_code == 201
    anim = Animation.objects.get(name=name)
    return (anim, *anim.frames.order_by('frame_number'))


def _tiled_job(admin_client, project, asset, name):
    resp = admin_client.post('/api/tiled-jobs/', data={
        'name': name, 'project': str(project.pk), 'asset_id': asset.pk,
        'final_resolution_x': 64, 'final_resolution_y': 64,
        'tile_count_x': 2, 'tile_count_y': 2,
    }, format='json')
    assert resp.status_code == 201
    return TiledJob.objects.get(name=name)


def _png(size=(32, 32)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, (255, 0, 0, 255)).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.mark.django_db
class TestRefreshWritesFirst:

    def test_every_refresh_starts_with_a_write(self, admin_client, project, asset):
        anim, frame, _f2 = _tiled_animation(admin_client, project, asset, 'LockAnim1')
        tiled_job = _tiled_job(admin_client, project, asset, 'LockTile1')
        untiled = Animation.objects.create(
            project=project, asset=asset, name='LockUntiled',
            output_file_pattern='//r/f_####.png', start_frame=1, end_frame=1,
        )
        calls = [
            (refresh_tiled_job, tiled_job.pk, 'workers_tiledjob'),
            (refresh_untiled_animation, untiled.pk, 'workers_animation'),
            (refresh_animation_frame, frame.pk, 'workers_animationframe'),
            (refresh_tiled_animation, anim.pk, 'workers_animation'),
        ]
        for refresh, pk, table in calls:
            with CaptureQueriesContext(connection) as ctx:
                refresh(pk)
            first = _statements(ctx)[0]
            assert first.startswith('UPDATE'), (refresh.__name__, first)
            assert table in first and LOCK_SENTINEL in first

    def test_parent_locked_before_children_are_counted(self, admin_client, project, asset):
        """#217 review A: the parent lock comes before the child aggregate.

        Order must be: write-lock UPDATE, then the parent ``SELECT ... FOR
        UPDATE`` (FOR UPDATE is dropped on SQLite), then the COUNT over
        the children in a later statement.  On PostgreSQL READ COMMITTED a
        later statement takes a fresh snapshot, so a sibling that waited
        on the parent lock counts the first one's DONE (no write skew).
        """
        tiled_job = _tiled_job(admin_client, project, asset, 'LockOrder1')
        with CaptureQueriesContext(connection) as ctx:
            refresh_tiled_job(tiled_job.pk)
        sql = _statements(ctx)

        def index(predicate):
            return next(i for i, s in enumerate(sql) if predicate(s))

        lock = index(lambda s: s.startswith('UPDATE') and LOCK_SENTINEL in s)
        parent_read = index(lambda s: s.startswith('SELECT') and 'FROM "workers_tiledjob"' in s)
        aggregate = index(lambda s: 'COUNT(' in s and 'FROM "workers_job"' in s)
        assert lock < parent_read < aggregate, sql

    def test_unchanged_refresh_does_not_rewrite_parent(self, admin_client, project, asset):
        tiled_job = _tiled_job(admin_client, project, asset, 'LockTile2')
        tile = tiled_job.jobs.first()
        tile.status = JobStatus.DONE
        tile.save()  # parent -> RENDERING via the signal
        tiled_job.refresh_from_db()
        assert tiled_job.status == TiledJobStatus.RENDERING

        with CaptureQueriesContext(connection) as ctx:
            assert refresh_tiled_job(tiled_job.pk) is False
        updates = [s for s in _statements(ctx) if s.startswith('UPDATE')]
        assert len(updates) == 1 and LOCK_SENTINEL in updates[0]


@pytest.mark.django_db
class TestAssemblerSurvivesParentRefreshError:

    def test_frame_stays_done_when_parent_refresh_raises(
        self, admin_client, project, asset, settings, tmp_path,
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        anim, frame, _f2 = _tiled_animation(admin_client, project, asset, 'LockAsm01')
        tiles = list(Job.objects.filter(animation_frame=frame))
        for tile in tiles:
            tile.output_file.save(f'tile_{tile.pk}.png', ContentFile(_png()), save=False)
            Job.objects.filter(pk=tile.pk).update(
                status=JobStatus.DONE, output_file=tile.output_file.name,
            )

        locked = OperationalError('database is locked')
        with patch('workers.parent_dispatch.refresh_tiled_animation', side_effect=locked) as refresh, \
                patch.object(image_assembler.logger, 'exception') as log_exception:
            image_assembler.assemble_animation_frame_image(frame.pk)

        frame.refresh_from_db()
        assert frame.status == AnimationFrameStatus.DONE
        assert frame.error_message == ''
        assert frame.output_file
        assert refresh.called  # the post_save refresh did run, and failed
        assert log_exception.called
        # Cleanup still ran: tile files were removed after the DONE save.
        assert all(not job.output_file for job in Job.objects.filter(animation_frame=frame))
