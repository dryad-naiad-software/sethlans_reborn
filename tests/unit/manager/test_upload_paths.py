# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/models/upload_paths.py.

All instance objects are mocks — no Django DB access.
"""

import uuid
from unittest.mock import MagicMock

from workers.models.upload_paths import (
    asset_upload_path,
    job_output_upload_path,
    tiled_job_output_upload_path,
    animation_frame_output_upload_path,
    thumbnail_upload_path,
)


class TestAssetUploadPath:
    def test_includes_project_short_id(self, mock_asset):
        path = asset_upload_path(mock_asset, "scene.blend")
        # Project short id is first 8 chars
        assert "assets/12345678/" in path

    def test_preserves_extension(self, mock_asset):
        path = asset_upload_path(mock_asset, "scene.blend")
        assert path.endswith(".blend")

    def test_different_extension(self, mock_asset):
        path = asset_upload_path(mock_asset, "archive.zip")
        assert path.endswith(".zip")

    def test_no_extension(self, mock_asset):
        path = asset_upload_path(mock_asset, "noext")
        # Should end with empty extension
        assert path.startswith("assets/12345678/")

    def test_generates_unique_segment(self, mock_asset):
        path1 = asset_upload_path(mock_asset, "file.blend")
        path2 = asset_upload_path(mock_asset, "file.blend")
        # UUID segment should differ
        assert path1 != path2


class TestJobOutputUploadPath:
    def test_standalone_job_path(self, mock_job):
        path = job_output_upload_path(mock_job, "render.png")
        assert path.startswith("assets/12345678/outputs/")
        assert "render.png" in path
        assert "test-job-001" in path

    def test_animation_job_uses_animation_name(self, mock_job):
        mock_job.animation = MagicMock()
        mock_job.animation.name = "Walk Cycle"
        mock_job.animation.id = 99
        path = job_output_upload_path(mock_job, "frame_0001.png")
        assert "walk-cycle-99" in path
        assert "frame_0001.png" in path

    def test_path_starts_with_assets(self, mock_job):
        path = job_output_upload_path(mock_job, "out.exr")
        assert path.startswith("assets/")


class TestTiledJobOutputUploadPath:
    def test_includes_slugified_name(self, mock_tiled_job):
        path = tiled_job_output_upload_path(
            mock_tiled_job, "final.png"
        )
        assert "tiled-render-" in path
        assert "final.png" in path

    def test_starts_with_assets(self, mock_tiled_job):
        path = tiled_job_output_upload_path(
            mock_tiled_job, "output.png"
        )
        assert path.startswith("assets/12345678/outputs/")


class TestAnimationFrameOutputUploadPath:
    def test_includes_animation_slug(self, mock_animation_frame):
        path = animation_frame_output_upload_path(
            mock_animation_frame, "frame_0005.png"
        )
        assert "walk-cycle-99" in path
        assert "frame_0005.png" in path

    def test_starts_with_assets(self, mock_animation_frame):
        path = animation_frame_output_upload_path(
            mock_animation_frame, "out.png"
        )
        assert path.startswith("assets/12345678/outputs/")


class TestThumbnailUploadPath:
    def test_job_thumbnail_path(self, mock_job):
        path = thumbnail_upload_path(mock_job, "thumb.png")
        assert "thumbnails/" in path
        assert path.endswith(".png")

    def test_tiled_job_thumbnail(self, mock_tiled_job):
        path = thumbnail_upload_path(mock_tiled_job, "t.png")
        assert "thumbnails/" in path
        assert "12345678" in path

    def test_unknown_project_fallback(self):
        """Instance with no project/asset/animation attributes."""
        instance = MagicMock(spec=[])
        instance.name = "orphan"
        instance.pk = "abc"
        path = thumbnail_upload_path(instance, "t.png")
        assert "unknown_project" in path

    def test_uuid_pk_truncated(self, mock_tiled_job):
        mock_tiled_job.pk = uuid.UUID(
            "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        )
        path = thumbnail_upload_path(mock_tiled_job, "t.png")
        # UUID should be truncated to 8 chars
        assert "aaaabbbb" in path

    def test_extension_defaults_to_png(self, mock_job):
        path = thumbnail_upload_path(mock_job, "noext")
        assert path.endswith(".png")
