# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Executor and thumbnail handling of the GPU device check (#238).

The generated render script exits with GPU_CHECK_EXIT_CODE and a marker
line on stderr when Blender did not enable the job's GPU. The executor must
turn that into a clear job error; a thumbnail pass must just drop the
thumbnail. A script that cannot be generated must still release the
Blender version the executor acquired.
"""
import logging

import pytest

from sethlans_worker_agent import blender_executor, blender_failure
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent.blender_thumbnail import render_exr_hdr_thumbnail
from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import CardInUse
from sethlans_worker_agent.gpu_device_script import (
    GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER,
)

_DETAIL = (
    "0 Blender device entries match OPTIX 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX'; "
    "wanted [OPTIX 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX']; "
    "Blender offers [CUDA 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00']"
)
_STDERR_LINES = [
    "Read blend: scene.blend\n",
    f"{GPU_CHECK_MARKER} {_DETAIL}\n",
]
_CARD = CardInUse('pci:0000:01:00.0', 'NVIDIA GeForce RTX 4080',
                  'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX', 'OPTIX')
_SLOT = SlotReservation(job_id=238, device_used='GPU', card_keys=(_CARD.key,), cards=(_CARD,))


def _fake_process(mocker, return_code, stderr_lines):
    """Return a Popen stand-in that exits at once with ``return_code``."""
    proc = mocker.Mock()
    proc.pid = 4242
    proc.poll.return_value = return_code
    proc.wait.return_value = return_code
    proc.stdout = mocker.Mock()
    proc.stdout.readline = mocker.Mock(return_value='')
    proc.stderr = mocker.Mock()
    proc.stderr.readline = mocker.Mock(side_effect=list(stderr_lines) + [''])
    return proc


@pytest.fixture
def executor_env(mocker, tmp_path):
    """Patch execute_blender_job's dependencies; return the tool manager mocks."""
    mocker.patch(
        'sethlans_worker_agent.asset_manager.ensure_asset_is_available',
        return_value=str(tmp_path / 'scene.blend'),
    )
    tm = 'sethlans_worker_agent.tool_manager.tool_manager_instance'
    mocker.patch(f'{tm}.get_blender_executable_path',
                 return_value=str(tmp_path / 'blender'))
    mocks = {
        'acquire': mocker.patch(f'{tm}.acquire_version'),
        'release': mocker.patch(f'{tm}.release_version'),
    }
    cfg = 'sethlans_worker_agent.config'
    mocker.patch(f'{cfg}.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
    mocker.patch(f'{cfg}.WORKER_OUTPUT_DIR', str(tmp_path / 'output'))
    mocker.patch(f'{cfg}.WORKER_ROOT', str(tmp_path))
    mocker.patch(f'{cfg}.CPU_THREADS', 0)

    mocker.patch(
        'sethlans_worker_agent.api_handler.get_job_status',
        return_value='RENDERING',
    )
    mocks['popen'] = mocker.patch('subprocess.Popen')
    return mocks


def _gpu_job():
    return {
        'id': 238,
        'name': 'GPU check',
        'render_device': 'GPU',
        'render_engine': 'CYCLES',
        'blender_version': '4.1.1',
        'start_frame': 1,
        'end_frame': 1,
        'output_file_pattern': 'render/frame_####.png',
        'asset': {'blend_file': 'http://127.0.0.1:8080/media/assets/scene.blend'},
        'render_settings': {},
    }


class TestExecutorGpuCheckFailure:

    def test_exit_87_with_marker_gives_gpu_check_error(self, mocker, executor_env):
        executor_env['popen'].return_value = _fake_process(
            mocker, GPU_CHECK_EXIT_CODE, _STDERR_LINES,
        )

        result = blender_executor.execute_blender_job(_gpu_job(), reservation=_SLOT)

        success, error_message, output_path = result[0], result[5], result[6]
        assert success is False
        assert output_path is None
        assert error_message.startswith('GPU device check failed:')
        # The OPTIX entry is missing, so the job error ends with the OptiX hint.
        assert error_message.endswith(f'{_DETAIL} {blender_failure.OPTIX_HINT}')
        assert 'Blender exited with code' not in error_message
        assert GPU_CHECK_MARKER in result[4]  # raw stderr is still returned
        # #239: the job thread asks for one immediate GPU list comparison.
        assert capacity_module.gpu_list_recheck_pending() is True

    def test_generated_script_carries_the_device_check(self, mocker, executor_env):
        """The real generator runs: the script written for Blender has the check."""
        written = []
        real_gen = blender_executor.generate_render_config_script

        def _capture(*args, **kwargs):
            script = real_gen(*args, **kwargs)
            written.append(script)
            return script

        mocker.patch.object(blender_executor, 'generate_render_config_script', side_effect=_capture)
        executor_env['popen'].return_value = _fake_process(mocker, 0, [])

        result = blender_executor.execute_blender_job(_gpu_job(), reservation=_SLOT)

        assert result[0] is True
        assert len(written) == 1
        assert '_sethlans_select_gpus(' in written[0]
        assert f"({_CARD.blender_id!r}, 'OPTIX')," in written[0]

    def test_other_failure_keeps_generic_message(self, mocker, executor_env):
        executor_env['popen'].return_value = _fake_process(mocker, 1, ["Error: boom\n"])

        result = blender_executor.execute_blender_job(_gpu_job(), reservation=_SLOT)

        assert result[5] == 'Blender exited with code 1. Details: Error: boom'
        assert capacity_module.gpu_list_recheck_pending() is False


class TestExecutorScriptGenerationReleasesVersion:

    def test_gpu_slot_without_card_releases_acquired_version(self, executor_env):
        """A GPU slot that holds no card: generation raises before Blender starts (FR-CB-3)."""
        result = blender_executor.execute_blender_job(
            _gpu_job(), reservation=SlotReservation(job_id=238, device_used='GPU'))

        assert result[0] is False
        assert result[5].startswith('Failed to generate render settings script:')
        assert "This job's slot holds no graphics card." in result[5]
        executor_env['acquire'].assert_called_once_with('4.1.1')
        executor_env['release'].assert_called_once_with('4.1.1')
        executor_env['popen'].assert_not_called()

    def test_generator_exception_releases_acquired_version(self, mocker, executor_env):
        mocker.patch.object(
            blender_executor, 'generate_render_config_script',
            side_effect=ValueError('GPU index 0 has no Blender device id'),
        )

        result = blender_executor.execute_blender_job(_gpu_job(), reservation=_SLOT)

        assert result[0] is False
        assert 'has no Blender device id' in result[5]
        executor_env['release'].assert_called_once_with('4.1.1')
        assert executor_env['acquire'].call_count == executor_env['release'].call_count


class TestThumbnailGpuCheckFailure:

    def test_exit_87_returns_none_and_logs_reason(self, mocker, tmp_path, caplog):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
        mocker.patch('sethlans_worker_agent.config.WORKER_ROOT', str(tmp_path))
        mocker.patch(
            'sethlans_worker_agent.blender_thumbnail.generate_render_config_script',
            return_value='import bpy',
        )
        mocker.patch('subprocess.Popen', return_value=_fake_process(
            mocker, GPU_CHECK_EXIT_CODE, _STDERR_LINES,
        ))
        job_data = dict(_gpu_job(), render_settings={
            'render.resolution_x': 1920,
            'render.resolution_y': 1080,
            'render.image_settings.file_format': 'OPEN_EXR',
        })

        with caplog.at_level(logging.WARNING, logger='sethlans_worker_agent.blender_thumbnail'):
            result = render_exr_hdr_thumbnail(
                job_data,
                str(tmp_path / 'blender'),
                str(tmp_path / 'scene.blend'),
                1,
                str(tmp_path / 'output' / 'render_####.exr'),
                _SLOT,
            )

        assert result is None
        assert 'Thumbnail render failed: GPU device check failed:' in caplog.text
        assert capacity_module.gpu_list_recheck_pending() is True
        assert _DETAIL in caplog.text
