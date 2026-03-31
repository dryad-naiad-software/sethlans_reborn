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
        self.url = "/api/tiled-jobs/"

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
        response = self.client.post(self.url, data, format='json')
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

    def test_create_tiled_job_name_too_short(self):
        """
        Tests that creating a tiled job with a name less than 4 characters fails.
        """
        data = {
            "name": "abc",
            "project": self.project.id,
            "asset_id": self.asset.id,
            "final_resolution_x": 10,
            "final_resolution_y": 10,
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("at least 4 characters", str(response.data['name']))

    def test_create_tiled_job_name_too_long(self):
        """
        Tests that creating a tiled job with a name more than 40 characters fails.
        """
        data = {
            "name": "a" * 41,
            "project": self.project.id,
            "asset_id": self.asset.id,
            "final_resolution_x": 10,
            "final_resolution_y": 10,
        }
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("more than 40 characters", str(response.data['name']))