# tests/unit/workers/test_manifest_generator.py
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/2/2025.
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
        project_short_id = str(self.project.id)[:8]
        manifest_path = Path(self.media_root) / 'assets' / project_short_id / 'manifest.txt'
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