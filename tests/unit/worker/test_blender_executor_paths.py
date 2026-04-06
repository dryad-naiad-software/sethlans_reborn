# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Regression tests for blender_executor output path resolution.

Bug #35: The Blender // prefix (relative-to-blend convention) was
being interpreted as a Windows UNC path. The fix strips leading
slashes before joining with the output directory.

Bug #36: The resolved output pattern already contains .png, so
appending + ".png" caused double extensions like .png.png. The fix
removed the redundant extension append.
"""

import os

import pytest


@pytest.fixture()
def _mock_executor_deps(mocker, tmp_path):
    """
    Patch all heavy dependencies of execute_blender_job so we can
    invoke it without real Blender, assets, or subprocesses.

    Returns the output dir path for assertion convenience.
    """
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
        'sethlans_worker_agent.config.WORKER_ROOT',
        str(tmp_path),
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

    # Stub subprocess so Blender never actually runs
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


def _make_job(output_file_pattern, start_frame=1, end_frame=1):
    """Build a minimal job_data dict with a given output pattern."""
    return {
        'id': 100,
        'name': 'PathTest',
        'render_device': 'CPU',
        'render_engine': 'CYCLES',
        'blender_version': '4.1.1',
        'start_frame': start_frame,
        'end_frame': end_frame,
        'output_file_pattern': output_file_pattern,
        'asset': {'blend_file': 'http://localhost/scene.blend'},
        'render_settings': {},
    }


class TestOutputPathSlashStripping:
    """
    Bug #35 regression: Blender prefixes output paths with //
    (meaning relative-to-.blend). On Windows os.path.join treats
    //host/share as a UNC path, producing wrong results.
    """

    def test_double_slash_prefix_does_not_produce_unc_path(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """Pattern //render/name_####.png must NOT produce a UNC path."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('//render/name_####.png')
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        assert output_path is not None
        # Must NOT start with \\\\ (UNC) or // (Unix-style double slash)
        assert not output_path.startswith('\\\\'), (
            f"Output path looks like a UNC path: {output_path}"
        )
        assert not output_path.startswith('//'), (
            f"Output path has unexpected double slash: {output_path}"
        )
        # Must be rooted under the worker output dir
        output_dir = str(tmp_path / 'output')
        assert output_path.startswith(output_dir), (
            f"Path {output_path} not under {output_dir}"
        )

    def test_single_slash_prefix_stripped(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """Pattern /render/name_####.png also strips the leading slash."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('/render/name_####.png')
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        output_dir = str(tmp_path / 'output')
        assert output_path.startswith(output_dir)

    def test_no_slash_prefix_works(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """Pattern render/name_####.png (no prefix) resolves correctly."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('render/name_####.png')
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        output_dir = str(tmp_path / 'output')
        assert output_path.startswith(output_dir)
        # The "render" subdirectory should appear in the final path
        assert 'render' in output_path

    def test_resolved_path_joins_with_worker_output_dir(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """The final path must be inside WORKER_OUTPUT_DIR regardless of prefix."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('//tiles/tile_####.png')
        success, _, _, _, _, output_path = execute_blender_job(job)

        expected_dir = os.path.normpath(
            str(tmp_path / 'output' / 'tiles')
        )
        assert os.path.dirname(output_path) == expected_dir


class TestOutputPathNoDoubleExtension:
    """
    Bug #36 regression: The old code appended + ".png" to the
    resolved pattern even though the pattern already ends with .png,
    producing files like render_0001.png.png.
    """

    def test_png_pattern_no_double_extension(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """Pattern ending in .png must NOT get a second .png appended."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('render/test_####.png', start_frame=7, end_frame=7)
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        assert output_path is not None
        assert output_path.endswith('.png'), (
            f"Expected .png extension, got: {output_path}"
        )
        assert not output_path.endswith('.png.png'), (
            f"Double .png extension detected: {output_path}"
        )

    def test_frame_placeholder_replaced_with_zero_padded_number(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """#### in the pattern becomes zero-padded frame number."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('render/frame_####.png', start_frame=42, end_frame=42)
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        assert '0042' in output_path
        assert '####' not in output_path

    def test_frame_1_produces_0001(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """Frame 1 should produce 0001 in the output filename."""
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('out/r_####.png', start_frame=1, end_frame=1)
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        assert output_path.endswith('r_0001.png')

    def test_multi_frame_job_returns_no_output_path(
        self, mocker, tmp_path, _mock_executor_deps,
    ):
        """
        Multi-frame jobs (start != end) do not produce a single
        output_path because Blender writes multiple files.
        """
        from sethlans_worker_agent.blender_executor import (
            execute_blender_job,
        )

        job = _make_job('out/r_####.png', start_frame=1, end_frame=10)
        success, _, _, _, _, output_path = execute_blender_job(job)

        assert success is True
        assert output_path is None
