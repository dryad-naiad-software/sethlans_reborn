# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for early-return error paths in execute_blender_job.

Bug #54: these paths previously returned 7-value tuples when the caller
expects 10.  The fix ensures all error returns are 10-tuples.  These
tests guard against regression.
"""

from sethlans_worker_agent import blender_stop
from sethlans_worker_agent.blender_executor import execute_blender_job

EXPECTED_TUPLE_LENGTH = 10


class TestAssetNotAvailable:
    """Error path: ensure_asset_is_available returns None."""

    def test_returns_10_tuple(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert isinstance(result, tuple)
        assert len(result) == EXPECTED_TUPLE_LENGTH

    def test_success_is_false(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert result[0] is False

    def test_was_canceled_is_false(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert result[1] is False

    def test_was_yielded_is_false(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert result[2] is False

    def test_error_message_not_empty(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert result[5], "error_msg (index 5) must be a non-empty string"

    def test_output_path_is_none(self, mocker, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR', '/tmp/w'
        )

        result = execute_blender_job(sample_job_data)

        assert result[6] is None


class TestBlenderVersionNotInstalled:
    """BS-WK-11 (D12): the job path never downloads Blender."""

    def test_missing_version_starts_no_download_and_no_render(self, mocker, sample_job_data):
        """BS-WK-11: a missing exact version installs nothing, fetches no asset, starts no Blender and
        marks the job for a ``blender_version_missing`` hand-back."""
        tm = 'sethlans_worker_agent.tool_manager.tool_manager_instance'
        lookup = mocker.patch(f'{tm}.get_blender_executable_path', return_value=None)
        install = mocker.patch('sethlans_worker_agent.blender_install.install')
        asset = mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available')
        acquire = mocker.patch(f'{tm}.acquire_version')
        popen = mocker.patch('subprocess.Popen')

        result = execute_blender_job(sample_job_data)

        lookup.assert_called_once_with('4.1.1')
        install.assert_not_called()
        asset.assert_not_called()
        acquire.assert_not_called()
        popen.assert_not_called()
        assert len(result) == EXPECTED_TUPLE_LENGTH and result[0] is False
        assert result[5] == blender_stop.STOPPED_MESSAGE
        assert sample_job_data[blender_stop.STOPPED_KEY] == blender_stop.BLENDER_VERSION_MISSING


class TestRenderScriptGenerationFailure:
    """Error path: generate_render_config_script raises an exception."""

    def _apply_mocks(self, mocker, tmp_path, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=str(tmp_path / 'scene.blend'),
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager'
            '.tool_manager_instance'
            '.get_blender_executable_path',
            return_value=str(tmp_path / 'blender'),
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager'
            '.tool_manager_instance.acquire_version',
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager'
            '.tool_manager_instance.release_version',
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
            'sethlans_worker_agent.config.CPU_THREADS', 0
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor'
            '.get_gpu_device_details',
            return_value=[],
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor'
            '.generate_render_config_script',
            side_effect=RuntimeError("bad render settings"),
        )

    def test_returns_10_tuple(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert isinstance(result, tuple)
        assert len(result) == EXPECTED_TUPLE_LENGTH

    def test_success_is_false(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[0] is False

    def test_was_canceled_is_false(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[1] is False

    def test_was_yielded_is_false(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[2] is False

    def test_error_message_contains_exception_text(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[5], "error_msg (index 5) must be non-empty"
        assert 'bad render settings' in result[5]

    def test_output_path_is_none(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[6] is None
