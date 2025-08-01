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

        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for y in range(2):
            for x in range(2):
                img = Image.new('RGB', (50, 50), color=colors[y * 2 + x])
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=self.media_root)
                img.save(temp_file, 'PNG')
                temp_file.close()

                job = Job.objects.create(
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
                job.output_file.name = os.path.relpath(temp_file.name, self.media_root)
                job.save()

    def test_assemble_animation_frame(self):
        assemble_animation_frame_image(self.frame1.id)
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)
        self.assertTrue(self.frame1.output_file.name)
        self.assertEqual(self.frame1.render_time_seconds, 40)

        final_image = Image.open(self.frame1.output_file.path)
        self.assertEqual(final_image.size, (100, 100))
        self.assertEqual(final_image.getpixel((25, 25)), (0, 0, 255, 255))
        self.assertEqual(final_image.getpixel((75, 75)), (0, 255, 0, 255))

    def test_animation_status_updates_after_all_frames_assemble(self):
        assemble_animation_frame_image(self.frame1.id)
        self.frame1.refresh_from_db()
        self.assertEqual(self.frame1.status, AnimationFrameStatus.DONE)

        self.animation.refresh_from_db()
        self.assertNotEqual(self.animation.status, "DONE")

        self.frame2.render_time_seconds = 60
        self.frame2.status = AnimationFrameStatus.DONE
        self.frame2.save()

        self.animation.refresh_from_db()
        self.assertEqual(self.animation.status, "DONE")
        self.assertIsNotNone(self.animation.completed_at)
        self.assertEqual(self.animation.total_render_time_seconds, 100)
