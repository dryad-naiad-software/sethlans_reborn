# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the render_script module.

Tests generation of Blender configuration scripts with various
engine, device, and settings combinations.
"""
import pytest

from sethlans_worker_agent.render_script import (
    generate_render_config_script,
    _resolve_target_gpu_index,
    _SAFE_KEY_RE,
)

# One record per physical card, as system_monitor.get_gpu_device_details()
# returns them: the card's preferred backend and Blender's id for it.
_RTX_OPTIX = {'index': 0, 'name': 'NVIDIA GeForce RTX 4080', 'type': 'OPTIX',
              'id': 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX'}
_GTX_CUDA = {'index': 1, 'name': 'NVIDIA GeForce GTX 1080', 'type': 'CUDA',
             'id': 'CUDA_NVIDIA GeForce GTX 1080_0000:02:00'}


def _mock_cards(mocker, cards):
    """Patch both detection calls to describe the given physical cards."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=sorted({c['type'] for c in cards})
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=cards
    )


class TestResolveTargetGpuIndex:

    def test_override_takes_priority(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', '2'
        )
        assert _resolve_target_gpu_index(0) == 0

    def test_config_force_gpu_index_used_when_no_override(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', '3'
        )
        assert _resolve_target_gpu_index(None) == 3

    def test_returns_none_when_nothing_set(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        assert _resolve_target_gpu_index(None) is None

    def test_invalid_force_gpu_index_returns_none(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', 'not-a-number'
        )
        assert _resolve_target_gpu_index(None) is None


class TestGenerateRenderConfigScript:

    def test_cycles_cpu_basic(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'CPU', {}
        )
        assert "import bpy" in script
        assert "_sethlans_set_engine(bpy.context.scene.render, 'CYCLES', ('CYCLES',))" in script
        assert "cycles.device = 'CPU'" in script

    def test_cycles_gpu_with_optix(self, mocker):
        _mock_cards(mocker, [_RTX_OPTIX, _GTX_CUDA])
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'GPU', {}
        )
        # Combined mode: the preferred backend, only its cards enabled.
        assert "'OPTIX', 'combined'," in script
        assert f"({_RTX_OPTIX['id']!r}, 'OPTIX')," in script
        assert f"({_GTX_CUDA['id']!r}, 'CUDA')," not in script
        # ...but the CUDA card is on the embedded card list, so OptiX can use it.
        assert f"({_GTX_CUDA['id']!r}, 'CUDA', {_GTX_CUDA['name']!r})," in script
        assert "cycles.device = 'GPU'" in script

    def test_cycles_gpu_cuda_fallback(self, mocker):
        _mock_cards(mocker, [_GTX_CUDA])
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'GPU', {}
        )
        assert "'CUDA', 'combined'," in script

    def test_cycles_any_with_gpu_available(self, mocker):
        _mock_cards(mocker, [_GTX_CUDA])
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'ANY', {}
        )
        assert "cycles.device = 'GPU'" in script

    def test_cycles_any_without_gpu(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'ANY', {}
        )
        assert "cycles.device = 'CPU'" in script

    def test_cpu_fallback_forces_cpu(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=['CUDA']
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'ANY', {},
            is_cpu_fallback=True
        )
        assert "cycles.device = 'CPU'" in script

    def test_gpu_isolation_with_index(self, mocker):
        _mock_cards(mocker, [_RTX_OPTIX, _GTX_CUDA])
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'GPU', {},
            gpu_index_override=1
        )
        # Index 1 is the second physical card: its own backend (CUDA,
        # not the machine-wide OPTIX) and its device id are used.
        assert "'CUDA', 'isolated'," in script
        assert f"({_GTX_CUDA['id']!r}, 'CUDA')," in script
        assert f"({_RTX_OPTIX['id']!r}, 'OPTIX')," not in script
        assert "non_cpu_devices" not in script

    def test_gpu_isolation_out_of_range_raises(self, mocker):
        _mock_cards(mocker, [_RTX_OPTIX])
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        with pytest.raises(ValueError, match="out of range"):
            generate_render_config_script(
                1, 'CYCLES', 'GPU', {},
                gpu_index_override=1
            )

    def test_eevee_engine_no_cycles_config(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'BLENDER_EEVEE_NEXT', 'GPU', {}
        )
        assert ("_sethlans_set_engine(bpy.context.scene.render, 'BLENDER_EEVEE_NEXT', "
                "('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'))") in script
        # EEVEE should not configure Cycles device settings
        assert 'cycles.device' not in script

    def test_workbench_engine(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'WORKBENCH', 'CPU', {}
        )
        assert ("_sethlans_set_engine(bpy.context.scene.render, 'WORKBENCH', "
                "('BLENDER_WORKBENCH',))") in script

    def test_invalid_engine_raises_valueerror(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        with pytest.raises(ValueError, match="Invalid render engine"):
            generate_render_config_script(
                1, 'INVALID_ENGINE', 'CPU', {}
            )

    def test_user_render_settings_applied(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        settings = {
            'render.resolution_x': 1920,
            'cycles.samples': 128,
        }
        script = generate_render_config_script(
            1, 'CYCLES', 'CPU', settings
        )
        assert "scene.render.resolution_x = 1920" in script
        assert "scene.cycles.samples = 128" in script

    def test_unsafe_setting_key_skipped(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        settings = {
            'valid_key': 100,
            'invalid key!': 200,
            '__import__("os")': 300,
        }
        script = generate_render_config_script(
            1, 'CYCLES', 'CPU', settings
        )
        assert "scene.valid_key = 100" in script
        assert 'invalid key!' not in script
        assert '__import__' not in script

    def test_empty_settings_no_overrides_section(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'CPU', {}
        )
        assert "user-defined" not in script

    def test_none_settings_no_overrides(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        script = generate_render_config_script(
            1, 'CYCLES', 'CPU', None
        )
        assert "user-defined" not in script


class TestSafeKeyRegex:

    def test_valid_keys(self):
        assert _SAFE_KEY_RE.match('render.resolution_x')
        assert _SAFE_KEY_RE.match('cycles.samples')
        assert _SAFE_KEY_RE.match('simple')
        assert _SAFE_KEY_RE.match('_private')

    def test_invalid_keys(self):
        assert not _SAFE_KEY_RE.match('')
        assert not _SAFE_KEY_RE.match('has space')
        assert not _SAFE_KEY_RE.match('123start')
        assert not _SAFE_KEY_RE.match('key;injection')
