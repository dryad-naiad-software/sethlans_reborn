# tests/unit/workers/test_manifest_generator.py
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
# Created by Gemini on 8/2/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
Unit tests for the project manifest generator utility.
"""

from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile
from workers.manifest_generator import update_project_manifest
from workers.models import Asset, Animation, TiledJob, Job
from workers.tests._base import BaseMediaTestCase


class ManifestGeneratorTests(BaseMediaTestCase):
    """
    Tests the content and creation of the project manifest file.
    """

    def test_manifest_generation(self):
        """
        Tests that a manifest is generated with the correct project, asset,
        and job information.
        """
        # Arrange: Create assets and jobs
        asset1 = Asset.objects.create(
            name="Scene Asset",
            project=self.project,
            blend_file=SimpleUploadedFile("scene.blend", b"data1")
        )
        asset2 = Asset.objects.create(
            name="Character Asset",
            project=self.project,
            blend_file=SimpleUploadedFile("char.blend", b"data2")
        )

        Animation.objects.create(name="Opening Scene", project=self.project, asset=asset1, start_frame=1, end_frame=10)
        # FIX: Added missing required fields `final_resolution_x` and `final_resolution_y`.
        TiledJob.objects.create(
            name="Character Portrait",
            project=self.project,
            asset=asset2,
            final_resolution_x=1920,
            final_resolution_y=1080
        )
        Job.objects.create(name="Test Render", asset=asset1, start_frame=1, end_frame=1)

        # Act: Generate the manifest
        update_project_manifest(self.project.id)

        # Assert: Check file content
        manifest_path = Path(self.media_root) / 'assets' / str(self.project.id) / 'manifest.txt'
        self.assertTrue(manifest_path.exists())

        content = manifest_path.read_text()
        self.assertIn(f"Project Name: {self.project.name}", content)
        self.assertIn(f"Project UUID: {self.project.id}", content)

        self.assertIn("- Scene Asset (File:", content)
        self.assertIn("- Character Asset (File:", content)

        self.assertIn("[Animation] Opening Scene", content)
        self.assertIn("  - Asset: Scene Asset", content)
        self.assertIn("[Tiled Job] Character Portrait", content)
        self.assertIn("  - Asset: Character Asset", content)
        self.assertIn("[Job] Test Render", content)
        self.assertIn("  - Asset: Scene Asset", content)