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
from ..models import Asset
from ._base import BaseMediaTestCase

class AssetViewSetTests(BaseMediaTestCase):
    def test_upload_asset_file(self):
        blend_file_content = b"this is a dummy blend file"
        uploaded_file = SimpleUploadedFile(
            "test_scene.blend", blend_file_content, content_type="application/octet-stream"
        )
        asset_data = {"name": "Test Scene Asset", "blend_file": uploaded_file, "project": self.project.id}
        url = "/api/assets/"
        response = self.client.post(url, asset_data, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Asset.objects.count(), 1)

        new_asset = Asset.objects.get()
        self.assertEqual(new_asset.name, "Test Scene Asset")
        self.assertEqual(new_asset.project, self.project)

        expected_path_start = f"assets/{self.project.id}/"
        self.assertTrue(new_asset.blend_file.name.startswith(expected_path_start))
        self.assertTrue(new_asset.blend_file.name.endswith(".blend"))
        self.assertNotIn("test_scene", new_asset.blend_file.name)

        with new_asset.blend_file.open('rb') as f:
            self.assertEqual(f.read(), blend_file_content)
