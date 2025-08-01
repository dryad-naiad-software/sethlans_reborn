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
from django.core.files.uploadedfile import SimpleUploadedFile
from ..models import Animation, AnimationFrame, Job, Asset
from ..constants import TilingConfiguration, RenderSettings
from ._base import BaseMediaTestCase

class TiledAnimationModelTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Anim Models",
            project=self.project,
            blend_file=SimpleUploadedFile("dummy_tiled_anim.blend", b"data"),
        )

    def test_create_tiled_animation_and_frames(self):
        anim = Animation.objects.create(
            name="Tiled Animation Model Test",
            project=self.project,
            asset=self.asset,
            start_frame=1,
            end_frame=2,
            tiling_config=TilingConfiguration.TILE_2X2,
        )
        self.assertEqual(anim.tiling_config, TilingConfiguration.TILE_2X2)

        anim_frame = AnimationFrame.objects.create(animation=anim, frame_number=1)
        self.assertEqual(anim_frame.animation, anim)

        job = Job.objects.create(
            name="Tiled Anim Job",
            asset=self.asset,
            animation=anim,
            animation_frame=anim_frame,
            start_frame=1,
            end_frame=1,
        )
        self.assertEqual(job.animation_frame, anim_frame)
        self.assertEqual(job.animation_frame.animation, anim)
        self.assertEqual(AnimationFrame.objects.count(), 1)
        self.assertEqual(anim.frames.count(), 1)
        self.assertEqual(anim_frame.tile_jobs.count(), 1)
