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
from rest_framework import status
from ..models import Animation, Job, Asset
from ..constants import RenderSettings, TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from ._base import BaseMediaTestCase

class AnimationViewSetTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Animations", project=self.project, blend_file=SimpleUploadedFile("dummy_anim.blend", b"data")
        )
        self.url = "/api/animations/"

    def test_create_animation_spawns_jobs(self):
        data = {
            "name": "My Test Animation",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/anim_####",
            "start_frame": 1,
            "end_frame": 5,
            "blender_version": "4.1.1",
            "render_device": RenderDevice.GPU,
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 5)
        parent = Animation.objects.first()
        self.assertEqual(parent.render_device, RenderDevice.GPU)
        self.assertEqual(parent.project, self.project)
        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_device, RenderDevice.GPU)
        self.assertEqual(first_job.asset, self.asset)

    def test_animation_progress_tracking(self):
        anim = Animation.objects.create(name="Progress Test", project=self.project, asset=self.asset, start_frame=1, end_frame=10)
        for i in range(1, 11):
            Job.objects.create(animation=anim, name=f"Job_{i}", asset=self.asset, start_frame=i, end_frame=i)
        Job.objects.filter(animation=anim, start_frame__in=[1, 2, 3]).update(status="DONE")
        url = f"/api/animations/{anim.id}/"
        response = self.client.get(url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_frames'], 10)
        self.assertEqual(response.data['completed_frames'], 3)
        self.assertEqual(response.data['progress'], "3 of 10 frames complete")

    def test_create_animation_propagates_render_settings(self):
        data = {
            "name": "Render Settings Test",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/settings_####",
            "start_frame": 1,
            "end_frame": 2,
            "render_settings": {"cycles.samples": 32, "render.resolution_x": 800},
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        parent = Animation.objects.get()
        self.assertEqual(parent.render_settings['cycles.samples'], 32)
        first_job = Job.objects.order_by('start_frame').first()
        self.assertEqual(first_job.render_settings['cycles.samples'], 32)
        self.assertEqual(first_job.render_settings['render.resolution_x'], 800)

    def test_create_tiled_animation_spawns_correct_jobs(self):
        data = {
            "name": "My Tiled Anim",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "//renders/tiled_anim_####",
            "start_frame": 1,
            "end_frame": 2,
            "tiling_config": TilingConfiguration.TILE_2X2,
            "render_settings": {RenderSettings.SAMPLES: 16},
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Animation.objects.count(), 1)
        # Frames and Jobs asserted in the model/assembly tests

    def test_create_animation_name_too_short(self):
        """
        Tests that creating an animation with a name less than 4 characters fails.
        """
        data = {
            "name": "abc",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "p",
            "start_frame": 1,
            "end_frame": 1,
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("at least 4 characters", str(response.data['name']))

    def test_create_animation_name_too_long(self):
        """
        Tests that creating an animation with a name more than 40 characters fails.
        """
        data = {
            "name": "a" * 41,
            "project": self.project.id,
            "asset_id": self.asset.id,
            "output_file_pattern": "p",
            "start_frame": 1,
            "end_frame": 1,
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("more than 40 characters", str(response.data['name']))