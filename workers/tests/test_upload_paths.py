# FILENAME: workers/tests/test_upload_paths.py
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
from django.utils.text import slugify
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
    Validates that all `upload_to` functions generate the correct, organized paths.
    """

    def setUp(self):
        """
        Set up the necessary models for testing path generation.
        """
        super().setUp()
        self.asset = Asset.objects.create(
            project=self.project,
            name="Path Test Asset",
            blend_file=SimpleUploadedFile("test.blend", b"data")
        )

    def test_asset_upload_path_is_unchanged(self):
        """
        Verifies asset paths use a short project ID for the directory and
        a new short UUID for the filename.
        """
        path = asset_upload_path(self.asset, "test.blend")
        parts = path.split('/')
        self.assertEqual(parts[0], 'assets')
        self.assertEqual(parts[1], str(self.project.id)[:8])
        self.assertTrue(parts[2].endswith('.blend'))
        self.assertEqual(len(parts[2]), 8 + 6) # 8 for short uuid + 6 for '.blend'

    def test_standalone_job_output_path_creates_descriptive_directory(self):
        """
        Verifies that a standalone job's output path is in a descriptive,
        slugified directory with its ID.
        """
        job = Job.objects.create(asset=self.asset, name="Test Standalone Job! #1", id=123)
        path = job_output_upload_path(job, "render-0001.png")
        slug = slugify(job.name)
        expected = f'assets/{str(self.project.id)[:8]}/outputs/{slug}-{job.id}/render-0001.png'
        self.assertEqual(path, expected)

    def test_animation_job_output_path_groups_by_descriptive_animation_name(self):
        """
        Verifies that frames from an animation are grouped under a descriptive,
        slugified directory based on the parent animation's name and ID.
        """
        anim = Animation.objects.create(
            project=self.project, asset=self.asset, name="My Cool Animation (Test)", start_frame=1, end_frame=2, id=999
        )
        job_frame1 = Job.objects.create(asset=self.asset, name="Frame 1", animation=anim, id=123)
        path = job_output_upload_path(job_frame1, "render-0001.png")
        slug = slugify(anim.name)
        anim_dir = f"{slug}-{anim.id}"
        expected = f'assets/{str(self.project.id)[:8]}/outputs/{anim_dir}/render-0001.png'
        self.assertEqual(path, expected)

    def test_tiled_job_output_upload_path_creates_descriptive_directory(self):
        """
        Verifies that tiled job output paths are created inside a descriptive,
        slugified directory with its short UUID.
        """
        tiled_job = TiledJob.objects.create(
            project=self.project, asset=self.asset, name="Test Tiled Job Path!", final_resolution_x=1, final_resolution_y=1
        )
        path = tiled_job_output_upload_path(tiled_job, "final_render.png")
        slug = slugify(tiled_job.name)
        job_dir = f"{slug}-{str(tiled_job.id)[:8]}"
        expected = f'assets/{str(self.project.id)[:8]}/outputs/{job_dir}/final_render.png'
        self.assertEqual(path, expected)

    def test_animation_frame_output_upload_path_creates_descriptive_animation_directory(self):
        """
        Verifies that assembled animation frame paths are created inside a
        descriptive, slugified directory based on the parent animation's name and ID.
        """
        anim = Animation.objects.create(
            project=self.project, asset=self.asset, name="Test Anim Frame Path", start_frame=1, end_frame=1, id=456
        )
        anim_frame = AnimationFrame.objects.create(animation=anim, frame_number=1)
        path = animation_frame_output_upload_path(anim_frame, "frame_0001.png")
        slug = slugify(anim.name)
        anim_dir = f"{slug}-{anim.id}"
        expected = f'assets/{str(self.project.id)[:8]}/outputs/{anim_dir}/frame_0001.png'
        self.assertEqual(path, expected)

    def test_job_thumbnail_upload_path(self):
        """
        Verifies thumbnail path for a standard Job.
        """
        job = Job.objects.create(asset=self.asset, name="My Job Thumbnail Test", id=555)
        path = thumbnail_upload_path(job, "thumb.png")
        slug = slugify(job.name)
        expected = f'assets/{str(self.project.id)[:8]}/thumbnails/{slug}-{job.id}_thumbnail.png'
        self.assertEqual(path, expected)

    def test_tiled_job_thumbnail_upload_path(self):
        """
        Verifies thumbnail path for a TiledJob.
        """
        tiled_job = TiledJob.objects.create(
            project=self.project, asset=self.asset, name="My Tiled Thumbnail", final_resolution_x=1, final_resolution_y=1
        )
        path = thumbnail_upload_path(tiled_job, "thumb.png")
        slug = slugify(tiled_job.name)
        short_id = str(tiled_job.id)[:8]
        expected = f'assets/{str(self.project.id)[:8]}/thumbnails/{slug}-{short_id}_thumbnail.png'
        self.assertEqual(path, expected)

    def test_animation_thumbnail_upload_path(self):
        """
        Verifies thumbnail path for an Animation.
        """
        anim = Animation.objects.create(
            project=self.project, asset=self.asset, name="My Animation Thumbnail", start_frame=1, end_frame=1
        )
        path = thumbnail_upload_path(anim, "thumb.png")
        slug = slugify(anim.name)
        expected = f'assets/{str(self.project.id)[:8]}/thumbnails/{slug}-{anim.id}_thumbnail.png'
        self.assertEqual(path, expected)

    def test_animation_frame_thumbnail_upload_path(self):
        """
        Verifies thumbnail path for an AnimationFrame is based on its parent animation.
        """
        anim = Animation.objects.create(
            project=self.project, asset=self.asset, name="Parent Anim For Frame", start_frame=1, end_frame=1, id=888
        )
        anim_frame = AnimationFrame.objects.create(animation=anim, frame_number=5)
        path = thumbnail_upload_path(anim_frame, "thumb.png")
        slug = slugify(anim.name)
        expected = f'assets/{str(self.project.id)[:8]}/thumbnails/{slug}-{anim.id}-frame-5_thumbnail.png'
        self.assertEqual(path, expected)