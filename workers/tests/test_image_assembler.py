# FILENAME: workers/tests/test_image_assembler.py
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
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
import os
import tempfile
from PIL import Image
from django.utils.text import slugify
from ..models import Job, TiledJob, Asset, JobStatus, TiledJobStatus
from ..image_assembler import assemble_tiled_job_image
from ._base import BaseMediaTestCase


class ImageAssemblerTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Assembler", project=self.project, blend_file=b"dummy"
        )
        self.tiled_job = TiledJob.objects.create(
            name="Test Assembly Job",
            project=self.project,
            asset=self.asset,
            final_resolution_x=200,
            final_resolution_y=200,
            tile_count_x=2,
            tile_count_y=2,
        )
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (100, 100), color=colors[y * 2 + x])
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=self.media_root)
                img.save(temp_file, 'PNG')
                temp_file.close()

                job = Job.objects.create(
                    tiled_job=self.tiled_job,
                    name=f"{self.tiled_job.name}_Tile_{y}_{x}",
                    asset=self.asset,
                    status=JobStatus.DONE,
                )
                job.output_file.name = os.path.relpath(temp_file.name, self.media_root)
                job.save()

    def test_assemble_tiled_job_image(self):
        assemble_tiled_job_image(self.tiled_job.id)
        self.tiled_job.refresh_from_db()
        self.assertEqual(self.tiled_job.status, TiledJobStatus.DONE)
        self.assertIsNotNone(self.tiled_job.completed_at)

        # Verify output file path
        project_short_id = str(self.tiled_job.project.id)[:8]
        slug = slugify(self.tiled_job.name)
        job_dir = f"{slug}-{str(self.tiled_job.id)[:8]}"
        self.assertTrue(self.tiled_job.output_file.name.startswith(f"assets/{project_short_id}/outputs/{job_dir}/"))

        # Verify thumbnail path and suffix
        base_thumb_stem = f'tiledjob_{str(self.tiled_job.id)[:8]}'
        expected_thumb_name_part = f'{base_thumb_stem}_thumbnail.png'
        self.assertIn(expected_thumb_name_part, self.tiled_job.thumbnail.name)

        # Verify image content
        final_image = Image.open(self.tiled_job.output_file.path)
        self.assertEqual(final_image.size, (200, 200))
        # Verify a few pixel colors to ensure tiles were placed correctly
        self.assertEqual(final_image.getpixel((50, 50)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((150, 50)), (255, 255, 0, 255))
        self.assertEqual(final_image.getpixel((50, 150)), (255, 0, 0, 255))
        self.assertEqual(final_image.getpixel((150, 150)), (0, 255, 0, 255))