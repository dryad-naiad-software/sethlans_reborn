# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the GPU part of render script generation (#238).

Verifies that GPU render scripts compile and embed the cards in use from
the job's slot reservation, selecting cards by backend and Blender device id.
"""

from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import CardInUse
from sethlans_worker_agent.render_script import generate_render_config_script


def _compile_script(script_text):
    """Compile a script string and return the code object or raise."""
    return compile(script_text, '<render_script>', 'exec')


def _card(index, backend, name, device_id):
    """One card in use, as the capacity profile holds it."""
    return CardInUse(f'pci:0000:{index + 1:02x}:00.0', name, device_id, backend)


def _slot(cards, index=None):
    """A GPU reservation: the card at ``index`` (split), or all of them (combined)."""
    own = cards if index is None else [cards[index]]
    return SlotReservation(job_id=1, device_used='GPU', card_keys=tuple(c.key for c in own),
                           cards=tuple(cards), gpu_mode='split' if index is not None else 'combined')


def test_cycles_gpu_script_compiles(mocker):
    """Cycles GPU render script is syntactically valid Python."""
    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
        reservation=_slot([_card(0, 'CUDA', 'GPU 0', 'CUDA_GPU_0')]),
    )

    code = _compile_script(script)
    assert code is not None


def test_cycles_gpu_configures_backend(mocker):
    """A combined slot mixing OptiX and CUDA cards renders through CUDA, every card by its CUDA entry."""
    cards = [
        _card(0, 'OPTIX', 'RTX', 'CUDA_RTX_0000:01:00_OptiX'),
        _card(1, 'CUDA', 'GTX', 'CUDA_GTX_0000:02:00'),
    ]

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
        reservation=_slot(cards),
    )

    _compile_script(script)
    assert "'CUDA', 'combined'," in script
    # Both cards must be found by their CUDA entry: the OptiX card by its id without "_OptiX".
    assert "('CUDA_RTX_0000:01:00', 'CUDA')," in script
    assert "('CUDA_GTX_0000:02:00', 'CUDA')," in script
    assert "('CUDA_RTX_0000:01:00_OptiX', 'OPTIX')," not in script
    assert "bpy.context.scene.cycles.device = 'GPU'" in script


def test_gpu_index_isolation_script(mocker):
    """GPU split mode isolates the physical card at the index by its id."""
    cards = [
        _card(0, 'CUDA', 'GPU 0', 'CUDA_GPU_0'),
        _card(1, 'CUDA', 'GPU 1', 'CUDA_GPU_1'),
        _card(2, 'HIP', 'GPU 2', 'HIP_GPU_2'),
    ]

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
        reservation=_slot(cards, 2),
    )

    code = _compile_script(script)
    assert code is not None
    assert "'HIP', 'isolated'," in script
    assert "('HIP_GPU_2', 'HIP')," in script
    assert "non_cpu_devices" not in script
