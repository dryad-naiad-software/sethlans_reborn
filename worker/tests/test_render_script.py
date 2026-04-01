# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the render_script module, focusing on normal script
generation: happy path and edge cases.
"""

import pytest

from sethlans_worker_agent import render_script


@pytest.fixture
def mock_gpu(mocker):
    """Mock GPU detection to return no GPUs (simplifies CPU-path tests)."""
    mocker.patch(
        "sethlans_worker_agent.system_monitor.detect_gpu_devices",
        return_value=[],
    )


# ------------------------------------------------------------------ #
# Script generation: happy path with valid render_settings
# ------------------------------------------------------------------ #

class TestRenderSettingsHappyPath:
    """Verify that valid render_settings are correctly interpolated."""

    def test_valid_settings_appear_in_script(self, mock_gpu):
        settings = {
            "render.resolution_x": 1920,
            "render.resolution_y": 1080,
            "cycles.samples": 128,
        }
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.render.resolution_x = 1920" in script
        assert "scene.render.resolution_y = 1080" in script
        assert "scene.cycles.samples = 128" in script

    def test_string_values_are_repr_quoted(self, mock_gpu):
        settings = {"render.image_settings.file_format": "PNG"}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.render.image_settings.file_format = 'PNG'" in script

    def test_boolean_values_are_python_booleans(self, mock_gpu):
        settings = {"render.film_transparent": True}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.render.film_transparent = True" in script

    def test_empty_settings_dict_produces_no_overrides(self, mock_gpu):
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings={},
        )

        assert "user-defined render settings" not in script

    def test_none_settings_produces_no_overrides(self, mock_gpu):
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=None,
        )

        assert "user-defined render settings" not in script


# ------------------------------------------------------------------ #
# Edge cases
# ------------------------------------------------------------------ #

class TestRenderSettingsEdgeCases:
    """Boundary and edge-case scenarios for render settings."""

    def test_single_char_key_is_valid(self, mock_gpu):
        settings = {"x": 42}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.x = 42" in script

    def test_underscore_only_key_is_valid(self, mock_gpu):
        settings = {"_": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene._ = 1" in script

    def test_deeply_nested_dotted_key_is_valid(self, mock_gpu):
        settings = {"a.b.c.d.e.f": 100}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.a.b.c.d.e.f = 100" in script

    def test_non_dict_settings_produces_no_overrides(self, mock_gpu):
        """A non-dict value for render_settings should be safely ignored."""
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings="not a dict",
        )

        assert "user-defined render settings" not in script

    def test_settings_with_zero_value(self, mock_gpu):
        settings = {"cycles.samples": 0}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.cycles.samples = 0" in script

    def test_settings_with_negative_value(self, mock_gpu):
        settings = {"render.resolution_x": -1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.render.resolution_x = -1" in script

    def test_settings_with_float_value(self, mock_gpu):
        settings = {"cycles.light_sampling_threshold": 0.01}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.cycles.light_sampling_threshold = 0.01" in script
