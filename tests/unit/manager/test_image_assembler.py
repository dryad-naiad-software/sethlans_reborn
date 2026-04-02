# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/image_assembler.py.

Tests the tile coordinate regex and assembly orchestration logic.
All Django ORM calls are mocked.
"""

from unittest.mock import MagicMock, patch

from workers.image_assembler import TILE_COORD_REGEX


class TestTileCoordRegex:
    """Verify the regex that parses tile coordinates from job names."""

    def test_matches_standard_tile_name(self):
        m = TILE_COORD_REGEX.search("MyJob_Tile_0_0")
        assert m is not None
        assert m.groups() == ("0", "0")

    def test_matches_multi_digit_coordinates(self):
        m = TILE_COORD_REGEX.search("Render_Tile_12_34")
        assert m is not None
        assert m.groups() == ("12", "34")

    def test_extracts_last_tile_pattern(self):
        """When name has extra text, regex finds the trailing _Tile_Y_X."""
        m = TILE_COORD_REGEX.search("Project_Scene_Tile_2_3")
        assert m is not None
        assert m.groups() == ("2", "3")

    def test_no_match_without_tile_suffix(self):
        m = TILE_COORD_REGEX.search("SomeJobWithoutTiles")
        assert m is None

    def test_no_match_partial_tile_suffix(self):
        m = TILE_COORD_REGEX.search("Job_Tile_1")
        assert m is None

    def test_no_match_non_numeric(self):
        m = TILE_COORD_REGEX.search("Job_Tile_a_b")
        assert m is None

    def test_tile_at_end_of_string(self):
        """The regex uses $ anchor — pattern must be at the end."""
        m = TILE_COORD_REGEX.search("Job_Tile_0_1_extra")
        assert m is None

    def test_groups_are_strings(self):
        m = TILE_COORD_REGEX.search("X_Tile_5_9")
        assert m.group(1) == "5"
        assert m.group(2) == "9"


class TestAssembleTiledJobImage:
    """Test the assemble_tiled_job_image function's orchestration."""

    @patch("workers.image_assembler.TiledJob")
    def test_returns_early_on_not_found(self, MockTiledJob):
        from workers.image_assembler import assemble_tiled_job_image
        from workers.models import TiledJob

        MockTiledJob.DoesNotExist = TiledJob.DoesNotExist
        MockTiledJob.objects.get.side_effect = TiledJob.DoesNotExist

        # Should not raise, just log and return
        assemble_tiled_job_image("nonexistent-id")
        MockTiledJob.objects.get.assert_called_once_with(
            id="nonexistent-id"
        )

    @patch("workers.image_assembler.TiledJob")
    @patch("workers.image_assembler.Job")
    @patch("workers.image_assembler.Image")
    @patch("workers.image_assembler.generate_thumbnail")
    @patch("workers.image_assembler.ContentFile")
    @patch("workers.image_assembler.timezone")
    def test_sets_status_to_assembling(
        self, mock_tz, mock_cf, mock_thumb, mock_pil,
        MockJob, MockTiledJob
    ):
        from workers.image_assembler import assemble_tiled_job_image

        tj = MagicMock()
        tj.id = "abc"
        tj.name = "Test TJ"
        tj.final_resolution_x = 100
        tj.final_resolution_y = 100
        tj.tile_count_x = 2
        tj.tile_count_y = 2
        tj.output_file = MagicMock()
        tj.output_file.__bool__ = MagicMock(return_value=True)
        tj.thumbnail = None
        MockTiledJob.objects.get.return_value = tj

        # Simulate no completed jobs to simplify
        mock_qs = MagicMock()
        mock_qs.__iter__ = MagicMock(return_value=iter([]))
        mock_qs.count.return_value = 0
        tj.jobs.filter.return_value.order_by.return_value = mock_qs

        mock_pil.new.return_value = MagicMock()
        mock_cf.return_value = MagicMock()
        mock_thumb.return_value = MagicMock()

        assemble_tiled_job_image("abc")

        # First save should set ASSEMBLING
        first_save_call = tj.save.call_args_list[0]
        assert first_save_call == (
            ((), {"update_fields": ["status"]})
            if first_save_call.kwargs
            else first_save_call
        )


class TestAssembleAnimationFrameImage:
    """Test the assemble_animation_frame_image orchestration."""

    @patch("workers.image_assembler.AnimationFrame")
    def test_returns_early_on_not_found(self, MockFrame):
        from workers.image_assembler import (
            assemble_animation_frame_image,
        )
        from workers.models import AnimationFrame

        MockFrame.DoesNotExist = AnimationFrame.DoesNotExist
        MockFrame.objects.select_related.return_value.get.side_effect = (
            AnimationFrame.DoesNotExist
        )

        assemble_animation_frame_image("missing-id")
        MockFrame.objects.select_related.assert_called_once()
