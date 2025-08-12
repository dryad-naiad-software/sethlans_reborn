# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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
