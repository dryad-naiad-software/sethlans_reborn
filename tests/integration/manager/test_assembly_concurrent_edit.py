# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Tile assembly does not overwrite edits made while it stitches (issue #225).

``assemble_tiled_job_image`` / ``assemble_animation_frame_image`` read the
parent row, stitch the tiles (seconds for a large image) and then saved
the DONE status with a full-row ``save()`` from that early copy, undoing
any change committed in between (an admin rename, a parent refresh).  The
DONE save now writes only the fields assembly owns.

The concurrent edit is committed from inside ``_stitch_tiles``, between
the read and the save.
"""

import io

import pytest
from PIL import Image

from workers import image_assembler
from workers.constants import TilingConfiguration
from workers.models import (
    Animation, AnimationFrame, AnimationFrameStatus, Job, JobStatus,
    TiledJob, TiledJobStatus,
)


def _png_bytes():
    buf = io.BytesIO()
    Image.new('RGBA', (8, 8), (255, 0, 0, 255)).save(buf, format='PNG')
    return buf.getvalue()


def _stitch_after(mocker, concurrent_edit):
    """Patch ``_stitch_tiles`` to commit ``concurrent_edit`` and return a PNG."""
    def fake_stitch(*_args, **_kwargs):
        concurrent_edit()
        return _png_bytes(), '.png'
    return mocker.patch.object(image_assembler, '_stitch_tiles', side_effect=fake_stitch)


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')


@pytest.mark.django_db
def test_admin_edit_to_tiled_job_during_assembly_survives(
    mocker, media_root, admin_client, project, asset,
):
    resp = admin_client.post('/api/tiled-jobs/', data={
        'name': 'AsmEdit001', 'project': str(project.pk), 'asset_id': asset.pk,
        'final_resolution_x': 8, 'final_resolution_y': 8,
        'tile_count_x': 2, 'tile_count_y': 2,
    }, format='json')
    assert resp.status_code == 201, resp.data
    tiled_job = TiledJob.objects.get(name='AsmEdit001')
    Job.objects.filter(tiled_job=tiled_job).update(status=JobStatus.DONE)
    _stitch_after(mocker, lambda: TiledJob.objects.filter(pk=tiled_job.pk).update(
        name='AsmEdited1', total_render_time_seconds=42,
    ))

    image_assembler.assemble_tiled_job_image(tiled_job.pk)

    tiled_job.refresh_from_db()
    assert tiled_job.status == TiledJobStatus.DONE
    assert tiled_job.output_file.name.endswith('.png')
    assert tiled_job.completed_at is not None
    assert tiled_job.name == 'AsmEdited1'
    assert tiled_job.total_render_time_seconds == 42


@pytest.mark.django_db
def test_concurrent_frame_change_during_assembly_survives(
    mocker, media_root, admin_client, project, asset,
):
    """A column the DONE save does not own keeps its committed value."""
    resp = admin_client.post('/api/animations/', data={
        'name': 'AsmEditAn1', 'project': str(project.pk), 'asset_id': asset.pk,
        'output_file_pattern': '//render/f_####.png',
        'start_frame': 1, 'end_frame': 1, 'frame_step': 1,
        'tiling_config': TilingConfiguration.TILE_2X2,
        'render_settings': {'render.resolution_x': 8, 'render.resolution_y': 8},
    }, format='json')
    assert resp.status_code == 201, resp.data
    frame = Animation.objects.get(name='AsmEditAn1').frames.get()
    Job.objects.filter(animation_frame=frame).update(
        status=JobStatus.DONE, render_time_seconds=3,
    )
    _stitch_after(mocker, lambda: AnimationFrame.objects.filter(pk=frame.pk).update(
        frame_number=7,
    ))

    image_assembler.assemble_animation_frame_image(frame.pk)

    frame.refresh_from_db()
    assert frame.status == AnimationFrameStatus.DONE
    assert frame.output_file.name.endswith('.png')
    assert frame.render_time_seconds == 12
    assert frame.frame_number == 7
