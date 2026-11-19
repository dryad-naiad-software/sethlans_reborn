# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the render_script module.

Tests generation of Blender configuration scripts with various
engine, device, and settings combinations. CPU or GPU and the cards come
from the job's slot reservation (FR-CB-1).
"""
import pytest

from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import CardInUse
from sethlans_worker_agent.render_script import (
    generate_render_config_script,
    _SAFE_KEY_RE,
)

# Cards in use, as the capacity profile holds them: the card's preferred
# backend and Blender's id for it.
_RTX_OPTIX = CardInUse('pci:0000:01:00.0', 'NVIDIA GeForce RTX 4080',
                       'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX', 'OPTIX')
_GTX_CUDA = CardInUse('pci:0000:02:00.0', 'NVIDIA GeForce GTX 1080',
                      'CUDA_NVIDIA GeForce GTX 1080_0000:02:00', 'CUDA')


def _gpu(cards, own, mode='split'):
    return SlotReservation(job_id=1, device_used='GPU', card_keys=tuple(c.key for c in own),
                           cards=tuple(cards), gpu_mode=mode)


_CPU = SlotReservation(job_id=1, device_used='CPU')


class TestGenerateRenderConfigScript:

    def test_cycles_cpu_basic(self):
        script = generate_render_config_script(1, 'CYCLES', 'CPU', {}, reservation=_CPU)
        assert "import bpy" in script
        assert "_sethlans_set_engine(bpy.context.scene.render, 'CYCLES', ('CYCLES',))" in script
        assert "cycles.device = 'CPU'" in script

    def test_cycles_gpu_combined_cuda_and_optix_uses_cuda(self):
        script = generate_render_config_script(
            1, 'CYCLES', 'GPU', {},
            reservation=_gpu([_RTX_OPTIX, _GTX_CUDA], [_RTX_OPTIX, _GTX_CUDA], 'combined'),
        )
        # A CUDA and OptiX mix: CUDA, and every card must be found by its CUDA entry.
        assert "'CUDA', 'combined'," in script
        assert f"({_RTX_OPTIX.blender_id.removesuffix('_OptiX')!r}, 'CUDA')," in script
        assert f"({_GTX_CUDA.blender_id!r}, 'CUDA')," in script
        assert f"({_RTX_OPTIX.blender_id!r}, 'OPTIX')," not in script
        assert "cycles.device = 'GPU'" in script

    def test_cycles_gpu_single_card(self):
        script = generate_render_config_script(1, 'CYCLES', 'GPU', {}, reservation=_gpu([_GTX_CUDA], [_GTX_CUDA]))
        assert "'CUDA', 'isolated'," in script

    def test_cycles_any_with_gpu_slot(self):
        script = generate_render_config_script(1, 'CYCLES', 'ANY', {}, reservation=_gpu([_GTX_CUDA], [_GTX_CUDA]))
        assert "cycles.device = 'GPU'" in script

    def test_cycles_any_on_cpu_slot(self):
        script = generate_render_config_script(1, 'CYCLES', 'ANY', {}, reservation=_CPU)
        assert "cycles.device = 'CPU'" in script

    def test_gpu_isolation_with_card(self):
        script = generate_render_config_script(
            1, 'CYCLES', 'GPU', {}, reservation=_gpu([_RTX_OPTIX, _GTX_CUDA], [_GTX_CUDA]),
        )
        # The second card: its own backend (CUDA, not OPTIX) and its device id are used.
        assert "'CUDA', 'isolated'," in script
        assert f"({_GTX_CUDA.blender_id!r}, 'CUDA')," in script
        assert f"({_RTX_OPTIX.blender_id!r}, 'OPTIX')," not in script
        assert "non_cpu_devices" not in script

    @pytest.mark.parametrize('reservation', [None, _CPU])
    def test_gpu_job_without_card_raises(self, reservation):
        with pytest.raises(ValueError, match="None of the ticked graphics cards can be used"):
            generate_render_config_script(1, 'CYCLES', 'GPU', {}, reservation=reservation)

    def test_gpu_slot_without_card_raises_the_slot_text(self):
        with pytest.raises(ValueError, match="This job's slot holds no graphics card."):
            generate_render_config_script(1, 'CYCLES', 'GPU', {}, reservation=SlotReservation(job_id=1,
                                                                                              device_used='GPU'))

    def test_eevee_engine_no_cycles_config(self):
        script = generate_render_config_script(1, 'BLENDER_EEVEE_NEXT', 'GPU', {})
        assert ("_sethlans_set_engine(bpy.context.scene.render, 'BLENDER_EEVEE_NEXT', "
                "('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'))") in script
        # EEVEE should not configure Cycles device settings
        assert 'cycles.device' not in script

    def test_workbench_engine(self):
        script = generate_render_config_script(1, 'WORKBENCH', 'CPU', {})
        assert ("_sethlans_set_engine(bpy.context.scene.render, 'WORKBENCH', "
                "('BLENDER_WORKBENCH',))") in script

    def test_invalid_engine_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid render engine"):
            generate_render_config_script(1, 'INVALID_ENGINE', 'CPU', {})

    def test_user_render_settings_applied(self):
        settings = {
            'render.resolution_x': 1920,
            'cycles.samples': 128,
        }
        script = generate_render_config_script(1, 'CYCLES', 'CPU', settings)
        assert "scene.render.resolution_x = 1920" in script
        assert "scene.cycles.samples = 128" in script

    def test_unsafe_setting_key_skipped(self):
        settings = {
            'valid_key': 100,
            'invalid key!': 200,
            '__import__("os")': 300,
        }
        script = generate_render_config_script(1, 'CYCLES', 'CPU', settings)
        assert "scene.valid_key = 100" in script
        assert 'invalid key!' not in script
        assert '__import__' not in script

    def test_empty_settings_no_overrides_section(self):
        script = generate_render_config_script(1, 'CYCLES', 'CPU', {})
        assert "user-defined" not in script

    def test_none_settings_no_overrides(self):
        script = generate_render_config_script(1, 'CYCLES', 'CPU', None)
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
