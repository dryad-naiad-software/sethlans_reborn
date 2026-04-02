# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/manifest_generator.py.

All Django ORM and filesystem calls are mocked.
"""

from unittest.mock import MagicMock, patch

from workers.manifest_generator import update_project_manifest


def _setup_manifest_mocks(
    MockProject, MockAsset, MockAnim, MockTJ, MockJob,
    mock_tz, mock_settings, project_id, project_name,
    assets=None, animations=None, tiled_jobs=None,
    standalone_jobs=None,
):
    """Shared mock setup for manifest tests."""
    project = MagicMock()
    project.id = project_id
    project.name = project_name
    MockProject.objects.get.return_value = project

    MockAsset.objects.filter.return_value = assets or []
    MockAnim.objects.filter.return_value = animations or []
    MockTJ.objects.filter.return_value = tiled_jobs or []
    MockJob.objects.filter.return_value = standalone_jobs or []

    mock_tz.now.return_value.strftime.return_value = (
        "2025-01-15 10:30:00 UTC"
    )
    mock_settings.MEDIA_ROOT = "/fake/media"
    return project


class TestUpdateProjectManifest:

    @patch("workers.manifest_generator.Project")
    def test_returns_early_on_missing_project(self, MockProject):
        from workers.models import Project
        MockProject.DoesNotExist = Project.DoesNotExist
        MockProject.objects.get.side_effect = Project.DoesNotExist

        # Should not raise
        update_project_manifest("nonexistent-id")
        MockProject.objects.get.assert_called_once_with(
            id="nonexistent-id"
        )

    @patch("workers.manifest_generator.settings")
    @patch("workers.manifest_generator.timezone")
    @patch("workers.manifest_generator.Job")
    @patch("workers.manifest_generator.TiledJob")
    @patch("workers.manifest_generator.Animation")
    @patch("workers.manifest_generator.Asset")
    @patch("workers.manifest_generator.Project")
    def test_writes_project_header(
        self, MockProject, MockAsset, MockAnim,
        MockTJ, MockJob, mock_tz, mock_settings, tmp_path
    ):
        mock_settings.MEDIA_ROOT = str(tmp_path)
        project = MagicMock()
        project.id = "12345678-abcd-1234-5678-abcdef012345"
        project.name = "My Render Project"
        MockProject.objects.get.return_value = project

        MockAsset.objects.filter.return_value = []
        MockAnim.objects.filter.return_value = []
        MockTJ.objects.filter.return_value = []
        MockJob.objects.filter.return_value = []
        mock_tz.now.return_value.strftime.return_value = (
            "2025-01-15 10:30:00 UTC"
        )

        update_project_manifest(
            "12345678-abcd-1234-5678-abcdef012345"
        )

        manifest = (
            tmp_path / "assets" / "12345678" / "manifest.txt"
        )
        assert manifest.exists()
        content = manifest.read_text(encoding="utf-8")
        assert "Project Manifest" in content
        assert "My Render Project" in content
        assert "12345678-abcd-1234-5678-abcdef012345" in content

    @patch("workers.manifest_generator.settings")
    @patch("workers.manifest_generator.timezone")
    @patch("workers.manifest_generator.Job")
    @patch("workers.manifest_generator.TiledJob")
    @patch("workers.manifest_generator.Animation")
    @patch("workers.manifest_generator.Asset")
    @patch("workers.manifest_generator.Project")
    def test_lists_assets_in_manifest(
        self, MockProject, MockAsset, MockAnim,
        MockTJ, MockJob, mock_tz, mock_settings, tmp_path
    ):
        mock_settings.MEDIA_ROOT = str(tmp_path)
        project = MagicMock()
        project.id = "aabbccdd-0000-0000-0000-000000000000"
        project.name = "Asset Test"
        MockProject.objects.get.return_value = project

        asset = MagicMock()
        asset.name = "Building Scene"
        asset.blend_file = MagicMock()
        asset.blend_file.name = "assets/aabbccdd/abc12345.blend"
        MockAsset.objects.filter.return_value = [asset]

        MockAnim.objects.filter.return_value = []
        MockTJ.objects.filter.return_value = []
        MockJob.objects.filter.return_value = []
        mock_tz.now.return_value.strftime.return_value = "2025-01-01"

        update_project_manifest(
            "aabbccdd-0000-0000-0000-000000000000"
        )

        manifest = (
            tmp_path / "assets" / "aabbccdd" / "manifest.txt"
        )
        content = manifest.read_text(encoding="utf-8")
        assert "Building Scene" in content
        assert "abc12345.blend" in content

    @patch("workers.manifest_generator.settings")
    @patch("workers.manifest_generator.timezone")
    @patch("workers.manifest_generator.Job")
    @patch("workers.manifest_generator.TiledJob")
    @patch("workers.manifest_generator.Animation")
    @patch("workers.manifest_generator.Asset")
    @patch("workers.manifest_generator.Project")
    def test_no_jobs_message(
        self, MockProject, MockAsset, MockAnim,
        MockTJ, MockJob, mock_tz, mock_settings, tmp_path
    ):
        mock_settings.MEDIA_ROOT = str(tmp_path)
        project = MagicMock()
        project.id = "11111111-0000-0000-0000-000000000000"
        project.name = "Empty"
        MockProject.objects.get.return_value = project

        MockAsset.objects.filter.return_value = []
        MockAnim.objects.filter.return_value = []
        MockTJ.objects.filter.return_value = []
        MockJob.objects.filter.return_value = []
        mock_tz.now.return_value.strftime.return_value = "2025-01-01"

        update_project_manifest(
            "11111111-0000-0000-0000-000000000000"
        )

        manifest = (
            tmp_path / "assets" / "11111111" / "manifest.txt"
        )
        content = manifest.read_text(encoding="utf-8")
        assert "No jobs found" in content

    @patch("workers.manifest_generator.settings")
    @patch("workers.manifest_generator.timezone")
    @patch("workers.manifest_generator.Job")
    @patch("workers.manifest_generator.TiledJob")
    @patch("workers.manifest_generator.Animation")
    @patch("workers.manifest_generator.Asset")
    @patch("workers.manifest_generator.Project")
    def test_includes_animations(
        self, MockProject, MockAsset, MockAnim,
        MockTJ, MockJob, mock_tz, mock_settings, tmp_path
    ):
        mock_settings.MEDIA_ROOT = str(tmp_path)
        project = MagicMock()
        project.id = "22222222-0000-0000-0000-000000000000"
        project.name = "Anim Project"
        MockProject.objects.get.return_value = project

        MockAsset.objects.filter.return_value = []
        anim = MagicMock()
        anim.name = "Walk Cycle"
        anim.asset.name = "Character"
        MockAnim.objects.filter.return_value = [anim]
        MockTJ.objects.filter.return_value = []
        MockJob.objects.filter.return_value = []
        mock_tz.now.return_value.strftime.return_value = "2025-01-01"

        update_project_manifest(
            "22222222-0000-0000-0000-000000000000"
        )

        manifest = (
            tmp_path / "assets" / "22222222" / "manifest.txt"
        )
        content = manifest.read_text(encoding="utf-8")
        assert "[Animation] Walk Cycle" in content
        assert "Character" in content

    @patch("workers.manifest_generator.settings")
    @patch("workers.manifest_generator.timezone")
    @patch("workers.manifest_generator.Job")
    @patch("workers.manifest_generator.TiledJob")
    @patch("workers.manifest_generator.Animation")
    @patch("workers.manifest_generator.Asset")
    @patch("workers.manifest_generator.Project")
    def test_includes_tiled_jobs(
        self, MockProject, MockAsset, MockAnim,
        MockTJ, MockJob, mock_tz, mock_settings, tmp_path
    ):
        mock_settings.MEDIA_ROOT = str(tmp_path)
        project = MagicMock()
        project.id = "33333333-0000-0000-0000-000000000000"
        project.name = "Tiled Project"
        MockProject.objects.get.return_value = project

        MockAsset.objects.filter.return_value = []
        MockAnim.objects.filter.return_value = []
        tj = MagicMock()
        tj.name = "Hi-Res Render"
        tj.asset.name = "Scene Asset"
        MockTJ.objects.filter.return_value = [tj]
        MockJob.objects.filter.return_value = []
        mock_tz.now.return_value.strftime.return_value = "2025-01-01"

        update_project_manifest(
            "33333333-0000-0000-0000-000000000000"
        )

        manifest = (
            tmp_path / "assets" / "33333333" / "manifest.txt"
        )
        content = manifest.read_text(encoding="utf-8")
        assert "[Tiled Job] Hi-Res Render" in content
        assert "Scene Asset" in content
