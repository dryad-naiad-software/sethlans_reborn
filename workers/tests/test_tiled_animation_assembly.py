# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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
from ..models import Animation, AnimationFrame, Job, AnimationFrameStatus, JobStatus, Asset
from ..constants import TilingConfiguration, RenderSettings
from ..image_assembler import assemble_animation_frame_image
from ._base import BaseMediaTestCase


class TiledAnimationAssemblyTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Assembly",
            project=self.project,
            blend_file=b"data",
        )
        self.animation = Animation.objects.create(
            name="Tiled Assembly Animation",
            project=self.project,
            asset=self.asset,
            start_frame=1,
            end_frame=2,
            tiling_config=TilingConfiguration.TILE_2X2,
        )
        self.frame1 = AnimationFrame.objects.create(animation=self.animation, frame_number=1)
        self.frame2 = AnimationFrame.objects.create(animation=self.animation, frame_number=2)

        self.tile_paths = []
        jobs_to_create = []
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (50, 50), color=colors[y * 2 + x])

                file_name = f"anim_tile_{y}_{x}.png"
                file_path = Path(self.media_root) / file_name
                img.save(file_path, 'PNG')
                self.tile_paths.append(file_path)

                job = Job(
                    animation=self.animation,
                    animation_frame=self.frame1,
                    name=f"{self.animation.name}_Frame_1_Tile_{y}_{x}",
                    asset=self.asset,
                    status=JobStatus.DONE,
                    render_time_seconds=10,
                    render_settings={
                        RenderSettings.RESOLUTION_X: 100,
                        RenderSettings.RESOLUTION_Y: 100,
                    },
                )
                job.output_file.name = file_name
                jobs_to_create.append(job)

        Job.objects.bulk_create(jobs_to_create)

    def test_assemble_animation_frame_and_cleanup_tiles(self):
        # Verify tiles exist before assembly
        for path in self.tile_paths:
            self.assertTrue(os.path.exists(path))

        # Run assembly
        assemble_animation_frame_image(self.frame1.id)
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)
        self.assertEqual(self.frame1.render_time_seconds, 40)

        # Verify final image content
        final_image = Image.open(self.frame1.output_file.path)
        self.assertEqual(final_image.size, (100, 100))
        self.assertEqual(final_image.getpixel((25, 25)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((75, 75)), (0, 255, 0, 255))

        # Verify tile files were deleted
        for path in self.tile_paths:
            self.assertFalse(os.path.exists(path), f"Tile file {path} was not deleted after assembly.")

        for job in self.frame1.tile_jobs.all():
            job.refresh_from_db()
            self.assertFalse(job.output_file, "Job output_file field should be cleared after deletion.")

    def test_animation_status_updates_after_all_frames_assemble(self):
        assemble_animation_frame_image(self.frame1.id)
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)

        self.animation.refresh_from_db()
        self.assertNotEqual(self.animation.status, "DONE")

        # Simulate the second frame completing
        self.frame2.render_time_seconds = 60
        self.frame2.status = AnimationFrameStatus.DONE
        self.frame2.save()  # This triggers the handle_animation_frame_completion signal

        self.animation.refresh_from_db()
        self.assertEqual(self.animation.status, "DONE")
        self.assertIsNotNone(self.animation.completed_at)
        self.assertEqual(self.animation.total_render_time_seconds, 100)