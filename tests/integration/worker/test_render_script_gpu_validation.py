# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the GPU part of render script generation (#238).

Verifies that GPU render scripts compile and embed the worker's physical
card list, selecting cards by backend and Blender device id.
"""

from sethlans_worker_agent.render_script import generate_render_config_script


def _compile_script(script_text):
    """Compile a script string and return the code object or raise."""
    return compile(script_text, '<render_script>', 'exec')


def _card(index, backend, name, device_id):
    """One physical card record as get_gpu_device_details() returns it."""
    return {'index': index, 'name': name, 'type': backend, 'id': device_id}


def _mock_cards(mocker, cards):
    """Patch both detection calls to describe the given physical cards."""
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.detect_gpu_devices',
        return_value=sorted({c['type'] for c in cards}),
    )
    mocker.patch(
        'sethlans_worker_agent.render_script.system_monitor'
        '.get_gpu_device_details',
        return_value=cards,
    )


def test_cycles_gpu_script_compiles(mocker):
    """Cycles GPU render script is syntactically valid Python."""
    _mock_cards(mocker, [_card(0, 'CUDA', 'GPU 0', 'CUDA_GPU_0')])

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
    )

    code = _compile_script(script)
    assert code is not None


def test_cycles_gpu_configures_backend(mocker):
    """Cycles GPU script selects the preferred backend for compute_device_type."""
    _mock_cards(mocker, [
        _card(0, 'OPTIX', 'RTX', 'CUDA_RTX_0000:01:00_OptiX'),
        _card(1, 'CUDA', 'GTX', 'CUDA_GTX_0000:02:00'),
    ])

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
    )

    _compile_script(script)
    assert "'OPTIX', 'combined'," in script
    # The card listed under CUDA is on the embedded card list (combined mode
    # enables every OPTIX entry of a listed card), not among the required cards.
    assert "('CUDA_GTX_0000:02:00', 'CUDA', 'GTX')," in script
    assert "('CUDA_GTX_0000:02:00', 'CUDA')," not in script
    assert "bpy.context.scene.cycles.device = 'GPU'" in script


def test_gpu_index_isolation_script(mocker):
    """GPU split mode isolates the physical card at the index by its id."""
    _mock_cards(mocker, [
        _card(0, 'CUDA', 'GPU 0', 'CUDA_GPU_0'),
        _card(1, 'CUDA', 'GPU 1', 'CUDA_GPU_1'),
        _card(2, 'HIP', 'GPU 2', 'HIP_GPU_2'),
    ])
    mocker.patch(
        'sethlans_worker_agent.render_script.config.FORCE_GPU_INDEX',
        None,
    )

    script = generate_render_config_script(
        job_id=1,
        render_engine='CYCLES',
        render_device='GPU',
        render_settings={},
        gpu_index_override=2,
    )

    code = _compile_script(script)
    assert code is not None
    assert "'HIP', 'isolated'," in script
    assert "('HIP_GPU_2', 'HIP')," in script
    assert "non_cpu_devices" not in script
