# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for early-return error paths in execute_blender_job.

Bug #54: these paths previously returned 7-value tuples when the caller
expects 10.  The fix ensures all error returns are 10-tuples.  These
tests guard against regression.
"""

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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )

        result = execute_blender_job(sample_job_data)

        assert result[6] is None


class TestBlenderVersionNotAvailable:
    """Error path: ensure_blender_version_available returns None."""

    def _apply_mocks(self, mocker, tmp_path, sample_job_data):
        mocker.patch(
            'sethlans_worker_agent.asset_manager'
            '.ensure_asset_is_available',
            return_value=str(tmp_path / 'scene.blend'),
        )
        mocker.patch(
            'sethlans_worker_agent.tool_manager'
            '.tool_manager_instance'
            '.ensure_blender_version_available',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp'),
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
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

    def test_error_message_mentions_version(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[5], "error_msg (index 5) must be non-empty"
        assert '4.1.1' in result[5]

    def test_output_path_is_none(
        self, mocker, tmp_path, sample_job_data
    ):
        self._apply_mocks(mocker, tmp_path, sample_job_data)

        result = execute_blender_job(sample_job_data)

        assert result[6] is None


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
            '.ensure_blender_version_available',
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
            'sethlans_worker_agent.config.FORCE_GPU_INDEX', None
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_CPU_ONLY', False
        )
        mocker.patch(
            'sethlans_worker_agent.config.FORCE_GPU_ONLY', False
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
