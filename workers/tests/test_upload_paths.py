# workers/tests/test_upload_paths.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Gemini on 8/3/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Unit tests for the custom upload path generation functions.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from ..models.upload_paths import (
    asset_upload_path,
    job_output_upload_path,
    tiled_job_output_upload_path,
    animation_frame_output_upload_path,
    thumbnail_upload_path,
)
from ._base import BaseMediaTestCase
from ..models import Asset, Job, TiledJob, AnimationFrame, Animation


class UploadPathTests(BaseMediaTestCase):
    """
    Validates that all `upload_to` functions generate paths with short UUIDs.
    """

    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            project=self.project,
            name="Path Test Asset",
            blend_file=SimpleUploadedFile("test.blend", b"data")
        )

    def test_asset_upload_path_uses_short_uuid(self):
        """
        Verifies asset paths use a short project ID for the directory and
        a new short UUID for the filename.
        """
        path = asset_upload_path(self.asset, "test.blend")
        parts = path.split('/')
        self.assertEqual(parts[0], 'assets')
        self.assertEqual(parts[1], str(self.project.id)[:8])
        self.assertTrue(parts[2].endswith('.blend'))
        # 8 chars for short uuid + 6 for '.blend'
        self.assertEqual(len(parts[2]), 8 + 6)

    def test_job_output_upload_path_uses_short_uuid(self):
        """
        Verifies job output paths use the short project ID.
        """
        job = Job.objects.create(asset=self.asset, name="Test Job", id=123)
        path = job_output_upload_path(job, "render.png")
        expected = f'assets/{str(self.project.id)[:8]}/outputs/job_123.png'
        self.assertEqual(path, expected)

    def test_tiled_job_output_upload_path_uses_short_uuid(self):
        """
        Verifies tiled job output paths use short project and short tiled job IDs.
        """
        tiled_job = TiledJob.objects.create(
            project=self.project, asset=self.asset, name="Test Tiled Job", final_resolution_x=1, final_resolution_y=1
        )
        path = tiled_job_output_upload_path(tiled_job, "render.png")
        expected = f'assets/{str(self.project.id)[:8]}/outputs/tiled_{str(tiled_job.id)[:8]}.png'
        self.assertEqual(path, expected)

    def test_animation_frame_output_upload_path_uses_short_uuid(self):
        """
        Verifies animation frame paths use the short project ID.
        """
        anim = Animation.objects.create(
            project=self.project, asset=self.asset, name="Test Animation", start_frame=1, end_frame=1, id=456
        )
        anim_frame = AnimationFrame.objects.create(animation=anim, frame_number=1)
        path = animation_frame_output_upload_path(anim_frame, "render.png")
        expected = f'assets/{str(self.project.id)[:8]}/outputs/anim_456/frame_0001.png'
        self.assertEqual(path, expected)

    def test_thumbnail_upload_path_uses_short_uuid(self):
        """
        Verifies thumbnail paths use the short project ID.
        """
        tiled_job = TiledJob.objects.create(
            project=self.project, asset=self.asset, name="Thumb Test", final_resolution_x=1, final_resolution_y=1
        )
        path = thumbnail_upload_path(tiled_job, "thumb.png")
        expected = f'assets/{str(self.project.id)[:8]}/thumbnails/tiledjob_{str(tiled_job.id)[:8]}.png'
        self.assertEqual(path, expected)