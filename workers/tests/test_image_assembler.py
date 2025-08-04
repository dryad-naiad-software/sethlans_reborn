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
from pathlib import Path
from PIL import Image
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
        self.tile_paths = []
        jobs_to_create = []
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (100, 100), color=colors[y * 2 + x])

                file_name = f"tile_{y}_{x}.png"
                file_path = Path(self.media_root) / file_name
                img.save(file_path, 'PNG')
                self.tile_paths.append(file_path)

                job = Job(
                    tiled_job=self.tiled_job,
                    name=f"{self.tiled_job.name}_Tile_{y}_{x}",
                    asset=self.asset,
                    status=JobStatus.DONE,
                )
                job.output_file.name = file_name
                jobs_to_create.append(job)

        Job.objects.bulk_create(jobs_to_create)

    def test_assemble_tiled_job_image_and_cleanup_tiles(self):
        # Verify tiles exist before assembly
        for path in self.tile_paths:
            self.assertTrue(os.path.exists(path))

        # Run assembly
        assemble_tiled_job_image(self.tiled_job.id)

        self.tiled_job.refresh_from_db()
        self.assertEqual(self.tiled_job.status, TiledJobStatus.DONE)
        self.assertIsNotNone(self.tiled_job.completed_at)

        # Verify final filename uses the short UUID
        short_uuid = str(self.tiled_job.id)[:8]
        expected_filename = f"tiled_job_{short_uuid}_final.png"
        self.assertIn(expected_filename, self.tiled_job.output_file.name)

        # Verify final image content
        final_image = Image.open(self.tiled_job.output_file.path)
        self.assertEqual(final_image.size, (200, 200))
        self.assertEqual(final_image.getpixel((50, 50)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((150, 150)), (0, 255, 0, 255))

        # Verify tile files were deleted
        for path in self.tile_paths:
            self.assertFalse(os.path.exists(path), f"Tile file {path} was not deleted after assembly.")

        for job in self.tiled_job.jobs.all():
            job.refresh_from_db()
            self.assertFalse(job.output_file, "Job output_file field should be cleared after deletion.")