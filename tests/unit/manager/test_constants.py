# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/constants.py.

Verifies enum values match the exact strings Blender's Python API expects.
"""

from workers.constants import (
    RenderEngine,
    CyclesFeatureSet,
    RenderDevice,
    TilingConfiguration,
    RenderSettings,
)


# ---- RenderEngine ----

class TestRenderEngine:
    def test_cycles_value(self):
        assert RenderEngine.CYCLES.value == "CYCLES"

    def test_eevee_value(self):
        assert RenderEngine.EEVEE.value == "BLENDER_EEVEE_NEXT"

    def test_workbench_value(self):
        assert RenderEngine.WORKBENCH.value == "WORKBENCH"

    def test_choices_count(self):
        assert len(RenderEngine.choices) == 3

    def test_labels(self):
        assert RenderEngine.CYCLES.label == "Cycles"
        assert RenderEngine.EEVEE.label == "Eevee"
        assert RenderEngine.WORKBENCH.label == "Workbench"


# ---- CyclesFeatureSet ----

class TestCyclesFeatureSet:
    def test_supported_value(self):
        assert CyclesFeatureSet.SUPPORTED.value == "SUPPORTED"

    def test_experimental_value(self):
        assert CyclesFeatureSet.EXPERIMENTAL.value == "EXPERIMENTAL"

    def test_choices_count(self):
        assert len(CyclesFeatureSet.choices) == 2


# ---- RenderDevice ----

class TestRenderDevice:
    def test_cpu_value(self):
        assert RenderDevice.CPU.value == "CPU"

    def test_gpu_value(self):
        assert RenderDevice.GPU.value == "GPU"

    def test_any_value(self):
        assert RenderDevice.ANY.value == "ANY"

    def test_choices_count(self):
        assert len(RenderDevice.choices) == 3


# ---- TilingConfiguration ----

class TestTilingConfiguration:
    def test_none_value(self):
        assert TilingConfiguration.NONE.value == "NONE"

    def test_2x2(self):
        assert TilingConfiguration.TILE_2X2.value == "2x2"

    def test_3x3(self):
        assert TilingConfiguration.TILE_3X3.value == "3x3"

    def test_4x4(self):
        assert TilingConfiguration.TILE_4X4.value == "4x4"

    def test_5x5(self):
        assert TilingConfiguration.TILE_5X5.value == "5x5"

    def test_choices_count(self):
        assert len(TilingConfiguration.choices) == 5

    def test_parseable_grid_values(self):
        """All non-NONE values must parse as NxN integers."""
        for choice_val, _label in TilingConfiguration.choices:
            if choice_val == "NONE":
                continue
            parts = choice_val.split("x")
            assert len(parts) == 2
            x, y = int(parts[0]), int(parts[1])
            assert x > 0
            assert y > 0


# ---- RenderSettings keys ----

class TestRenderSettings:
    def test_resolution_keys(self):
        assert RenderSettings.RESOLUTION_X == "render.resolution_x"
        assert RenderSettings.RESOLUTION_Y == "render.resolution_y"

    def test_engine_key(self):
        assert RenderSettings.RENDER_ENGINE == "render.engine"

    def test_samples_key(self):
        assert RenderSettings.SAMPLES == "cycles.samples"

    def test_border_keys_exist(self):
        assert hasattr(RenderSettings, "BORDER_MIN_X")
        assert hasattr(RenderSettings, "BORDER_MAX_X")
        assert hasattr(RenderSettings, "BORDER_MIN_Y")
        assert hasattr(RenderSettings, "BORDER_MAX_Y")

    def test_cycles_device_key(self):
        assert RenderSettings.CYCLES_DEVICE == "cycles.device"

    def test_all_keys_are_dot_separated(self):
        """Every setting key should be 'namespace.property' format."""
        for attr in dir(RenderSettings):
            if attr.startswith("_"):
                continue
            val = getattr(RenderSettings, attr)
            assert "." in val, f"{attr}={val} is not dot-separated"
