# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for output format support in the worker agent.

Tests the dynamic -F flag in blender_executor, thumbnail upload in
api_handler, and thumbnail cleanup in job_processor.
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
        '.ensure_blender_version_available',
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
    mocker.patch('sethlans_worker_agent.config.FORCE_CPU_ONLY', False)
    mocker.patch('sethlans_worker_agent.config.FORCE_GPU_ONLY', False)
    mocker.patch('sethlans_worker_agent.config.FORCE_GPU_INDEX', None)
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

    def test_7_tuple_return(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """execute_blender_job returns a 7-tuple."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job(None, 'out/test_####.png')
        result = execute_blender_job(job)
        assert len(result) == 7

    def test_thumbnail_path_none_for_png(
        self, mocker, tmp_path, _mock_executor_deps
    ):
        """PNG jobs should have thumbnail_path=None."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )
        job = _make_job('PNG', 'out/test_####.png')
        result = execute_blender_job(job)
        assert result[6] is None


class TestUploadWithThumbnail:

    def test_upload_includes_thumbnail(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')
        thumb_file = tmp_path / 'thumb.png'
        thumb_file.write_bytes(b'PNG data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(func, *args, **kw):
            files = kw.get('files')
            if files:
                for key, val in files.items():
                    captured_files[key] = val[0]  # filename
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(
            1, str(out_file), thumbnail_path=str(thumb_file)
        )
        assert result is True
        assert 'output_file' in captured_files
        assert 'thumbnail' in captured_files
        assert captured_files['thumbnail'] == 'thumb.png'

    def test_upload_without_thumbnail(self, mocker, tmp_path):
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.png'
        out_file.write_bytes(b'PNG data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(func, *args, **kw):
            files = kw.get('files')
            if files:
                for key in files:
                    captured_files[key] = True
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(1, str(out_file))
        assert result is True
        assert 'output_file' in captured_files
        assert 'thumbnail' not in captured_files

    def test_thumbnail_ioerror_still_uploads(self, mocker, tmp_path):
        """IOError on thumbnail should not prevent output upload."""
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')
        # Thumbnail path that does not exist
        thumb_path = str(tmp_path / 'nonexistent_thumb.png')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_files = {}

        def capture_retry(func, *args, **kw):
            files = kw.get('files')
            if files:
                for key in files:
                    captured_files[key] = True
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        result = upload_render_output(
            1, str(out_file), thumbnail_path=thumb_path
        )
        assert result is True
        assert 'output_file' in captured_files
        # Thumbnail should not be included since file doesn't exist
        assert 'thumbnail' not in captured_files

    def test_mime_type_is_octet_stream(self, mocker, tmp_path):
        """Output file MIME type should be application/octet-stream."""
        mocker.patch(
            'sethlans_worker_agent.config.MANAGER_API_URL',
            'http://localhost:7075/api/'
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.get_auth_headers',
            return_value={'Authorization': 'Token test'}
        )
        mocker.patch(
            'sethlans_worker_agent.api_auth.handle_auth_response',
            return_value=False
        )

        out_file = tmp_path / 'render.exr'
        out_file.write_bytes(b'EXR data')

        mock_resp = mocker.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = mocker.Mock()

        captured_mime = {}

        def capture_retry(func, *args, **kw):
            files = kw.get('files')
            if files:
                for key, val in files.items():
                    captured_mime[key] = val[2]  # mime type
            return mock_resp

        mocker.patch(
            'sethlans_worker_agent.api_handler._retry_request',
            side_effect=capture_retry,
        )

        from sethlans_worker_agent.api_handler import (
            upload_render_output,
        )
        upload_render_output(1, str(out_file))
        assert captured_mime['output_file'] == 'application/octet-stream'
