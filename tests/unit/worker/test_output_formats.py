# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for output format support in the worker agent.

Tests the dynamic -F flag in blender_executor and the thumbnail path it
returns. The thumbnail upload in api_handler is in
test_upload_with_thumbnail.py.
"""
import pytest


@pytest.fixture()
def _mock_executor_deps(mocker, tmp_path):
    """Patch all heavy deps of execute_blender_job for format flag tests."""
    mocker.patch(
        'sethlans_worker_agent.asset_manager.ensure_asset_is_available',
        return_value=str(tmp_path / 'scene.blend'),
    )
    mocker.patch(
        'sethlans_worker_agent.tool_manager.tool_manager_instance'
        '.get_blender_executable_path',
        return_value=str(tmp_path / 'blender'),
    )
    mocker.patch(
        'sethlans_worker_agent.tool_manager.tool_manager_instance'
        '.acquire_version',
    )
    mocker.patch(
        'sethlans_worker_agent.tool_manager.tool_manager_instance'
        '.release_version',
    )
    mocker.patch(
        'sethlans_worker_agent.config.WORKER_TEMP_DIR',
        str(tmp_path / 'temp'),
    )
    mocker.patch(
        'sethlans_worker_agent.config.WORKER_OUTPUT_DIR',
        str(tmp_path / 'output'),
    )
    mocker.patch(
        'sethlans_worker_agent.config.WORKER_ROOT', str(tmp_path),
    )
    mocker.patch('sethlans_worker_agent.config.CPU_THREADS', 0)
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_cpu_thread_count',
        return_value=8,
    )
    mocker.patch(
        'sethlans_worker_agent.render_script'
        '.generate_render_config_script',
        return_value='import bpy',
    )

    mock_process = mocker.Mock()
    mock_process.poll.return_value = 0
    mock_process.wait.return_value = 0
    mock_process.pid = 12345
    mock_process.stdout = mocker.Mock()
    mock_process.stdout.readline = mocker.Mock(return_value='')
    mock_process.stderr = mocker.Mock()
    mock_process.stderr.readline = mocker.Mock(return_value='')
    mock_process.stdout.close = mocker.Mock()
    mock_process.stderr.close = mocker.Mock()
    mocker.patch('subprocess.Popen', return_value=mock_process)
    mocker.patch(
        'sethlans_worker_agent.api_handler.get_job_status',
        return_value='RENDERING',
    )


def _make_job(fmt, pattern, start=1, end=1):
    settings = {}
    if fmt:
        settings['render.image_settings.file_format'] = fmt
    return {
        'id': 200,
        'name': 'FmtTest',
        'render_device': 'CPU',
        'render_engine': 'CYCLES',
        'blender_version': '4.1.1',
        'start_frame': start,
        'end_frame': end,
        'output_file_pattern': pattern,
        'asset': {'blend_file': 'http://localhost/scene.blend'},
        'render_settings': settings,
    }


class TestDynamicFormatFlag:

    def test_jpeg_format_uses_f_jpeg(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """render_settings with JPEG should produce -F JPEG."""
        captured = []
        orig_popen = mocker.patch('subprocess.Popen')
        mock_proc = mocker.Mock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 12345
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr.close = mocker.Mock()

        def capture(cmd, **kw):
            captured.extend(cmd)
            return mock_proc

        orig_popen.side_effect = capture

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job('JPEG', 'out/test_####.jpg')
        execute_blender_job(job)

        idx = captured.index('-F')
        assert captured[idx + 1] == 'JPEG'

    def test_default_format_is_png(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """No format in render_settings defaults to -F PNG."""
        captured = []
        orig_popen = mocker.patch('subprocess.Popen')
        mock_proc = mocker.Mock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 12345
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr.close = mocker.Mock()

        def capture(cmd, **kw):
            captured.extend(cmd)
            return mock_proc

        orig_popen.side_effect = capture

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job(None, 'out/test_####.png')
        execute_blender_job(job)

        idx = captured.index('-F')
        assert captured[idx + 1] == 'PNG'

    def test_exr_format_uses_f_open_exr(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """OPEN_EXR format should produce -F OPEN_EXR."""
        captured = []
        orig_popen = mocker.patch('subprocess.Popen')
        mock_proc = mocker.Mock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.pid = 12345
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr.close = mocker.Mock()

        def capture(cmd, **kw):
            captured.extend(cmd)
            return mock_proc

        orig_popen.side_effect = capture

        # Patch thumbnail so it doesn't run a second subprocess
        mocker.patch(
            'sethlans_worker_agent.blender_thumbnail'
            '.render_exr_hdr_thumbnail',
            return_value=None,
        )

        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job('OPEN_EXR', 'out/test_####.exr')
        execute_blender_job(job)

        idx = captured.index('-F')
        assert captured[idx + 1] == 'OPEN_EXR'

    def test_10_tuple_return(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """execute_blender_job returns a 10-tuple."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job(None, 'out/test_####.png')
        result = execute_blender_job(job)
        assert len(result) == 10

    def test_thumbnail_path_none_for_png(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """PNG jobs should have thumbnail_path=None."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job('PNG', 'out/test_####.png')
        result = execute_blender_job(job)
        assert result[7] is None
