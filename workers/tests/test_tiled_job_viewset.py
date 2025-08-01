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
from ..models import TiledJob, Job, Asset
from ..constants import RenderSettings
from ._base import BaseMediaTestCase

class TiledJobViewSetTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        self.asset = Asset.objects.create(
            name="Test Asset for Tiled Jobs", project=self.project, blend_file=SimpleUploadedFile("dummy_tiled.blend", b"data")
        )

    def test_create_tiled_job_spawns_child_jobs(self):
        data = {
            "name": "My Tiled Render",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "final_resolution_x": 800,
            "final_resolution_y": 600,
            "tile_count_x": 2,
            "tile_count_y": 2,
            "render_settings": {RenderSettings.SAMPLES: 64},
        }
        url = "/api/tiled-jobs/"
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(TiledJob.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 4)

        job_tile_0_0 = Job.objects.get(name="My Tiled Render_Tile_0_0")
        s = job_tile_0_0.render_settings
        self.assertEqual(s[RenderSettings.SAMPLES], 64)
        self.assertEqual(s[RenderSettings.RESOLUTION_X], 800)
        self.assertEqual(s[RenderSettings.USE_BORDER], True)
        self.assertEqual(s[RenderSettings.BORDER_MIN_X], 0.0)
        self.assertEqual(s[RenderSettings.BORDER_MAX_X], 0.5)
        self.assertEqual(s[RenderSettings.BORDER_MIN_Y], 0.0)
        self.assertEqual(s[RenderSettings.BORDER_MAX_Y], 0.5)

        job_tile_1_1 = Job.objects.get(name="My Tiled Render_Tile_1_1")
        s = job_tile_1_1.render_settings
        self.assertEqual(s[RenderSettings.SAMPLES], 64)
        self.assertEqual(s[RenderSettings.BORDER_MIN_X], 0.5)
        self.assertEqual(s[RenderSettings.BORDER_MAX_X], 1.0)
        self.assertEqual(s[RenderSettings.BORDER_MIN_Y], 0.5)
        self.assertEqual(s[RenderSettings.BORDER_MAX_Y], 1.0)
