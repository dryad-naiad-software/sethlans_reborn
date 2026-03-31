# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 07/24/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/worker_agent/test_poll_and_claim.py

import pytest

from sethlans_worker_agent import job_processor, config


class TestPollAndClaimJob:
    @pytest.fixture
    def mock_poll_deps(self, mocker):
        """Mocks dependencies for poll_and_claim_job."""
        mocker.patch.object(config, 'FORCE_CPU_ONLY', False)
        mocker.patch.object(config, 'FORCE_GPU_ONLY', False)
        mocker.patch.object(config, 'GPU_SPLIT_MODE', False)
        mock_poll_api = mocker.patch(
            'sethlans_worker_agent.api_handler.poll_for_available_jobs',
            return_value=None
        )
        mock_detect_gpu = mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices'
        )
        return mock_poll_api, mock_detect_gpu

    def test_poll_in_force_cpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_CPU_ONLY mode should poll for gpu_available=false."""
        mock_poll_api, _ = mock_poll_deps
        mocker.patch.object(config, 'FORCE_CPU_ONLY', True)

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert call_params.get('gpu_available') == 'false'

    def test_poll_in_force_gpu_mode(self, mocker, mock_poll_deps):
        """Worker in FORCE_GPU_ONLY mode should poll for gpu_available=true."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']
        mocker.patch.object(config, 'FORCE_GPU_ONLY', True)

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert call_params.get('gpu_available') == 'true'

    def test_poll_in_default_mode(self, mock_poll_deps):
        """A normal worker should not specify gpu_available, making it flexible."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']

        job_processor.poll_and_claim_job(1)

        mock_poll_api.assert_called_once()
        call_params = mock_poll_api.call_args.args[0]
        assert 'gpu_available' not in call_params

    def test_reserve_next_available_gpu(self, mocker):
        """Tests the atomic find-and-reserve logic for the next free GPU."""
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[{}, {}]
        )

        # Case 1: No GPUs are busy — reserves GPU 0
        job_processor._gpu_assignment_map.clear()
        assert job_processor._reserve_next_available_gpu(100) == 0
        assert job_processor._gpu_assignment_map[0] == 100

        # Case 2: GPU 0 is busy — reserves GPU 1
        job_processor._gpu_assignment_map = {0: 123}
        assert job_processor._reserve_next_available_gpu(200) == 1
        assert job_processor._gpu_assignment_map[1] == 200

        # Case 3: All GPUs are busy — returns None
        job_processor._gpu_assignment_map = {0: 123, 1: 456}
        assert job_processor._reserve_next_available_gpu(300) is None

    def test_split_mode_claims_any_job_for_cpu_when_gpus_busy(
        self, mocker, mock_poll_deps
    ):
        """Tests the key bug fix: an 'ANY' job falls back to CPU if GPUs are full."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']
        mocker.patch.object(config, 'GPU_SPLIT_MODE', True)
        mocker.patch(
            'sethlans_worker_agent.job_processor._reserve_next_available_gpu',
            return_value=None
        )
        mock_claim_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job', return_value=True
        )

        mock_poll_api.return_value = [{'id': 7, 'render_device': 'ANY'}]

        result = job_processor.poll_and_claim_job(1)

        assert result is not None
        assert result['id'] == 7
        assert result['assigned_gpu_index'] is None
        assert result['_acquired_cpu_lock'] is True
        mock_claim_api.assert_called_once()

    def test_split_mode_skips_gpu_job_when_gpus_busy(
        self, mocker, mock_poll_deps
    ):
        """
        Tests that a 'GPU'-only job is correctly skipped if all GPUs are busy,
        without attempting a CPU fallback.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']
        mocker.patch.object(config, 'GPU_SPLIT_MODE', True)
        mocker.patch(
            'sethlans_worker_agent.job_processor._reserve_next_available_gpu',
            return_value=None
        )
        mock_claim_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job'
        )

        mock_poll_api.return_value = [{'id': 8, 'render_device': 'GPU'}]

        result = job_processor.poll_and_claim_job(1)

        assert result is None
        mock_claim_api.assert_not_called()

    def test_split_mode_skips_any_job_when_all_resources_busy(
        self, mocker, mock_poll_deps
    ):
        """Tests that an 'ANY' job is skipped if all GPUs AND the CPU are busy."""
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = ['CUDA']
        mocker.patch.object(config, 'GPU_SPLIT_MODE', True)
        mocker.patch(
            'sethlans_worker_agent.job_processor._reserve_next_available_gpu',
            return_value=None
        )
        mock_claim_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job'
        )

        mock_poll_api.return_value = [{'id': 9, 'render_device': 'ANY'}]

        job_processor._cpu_lock.acquire()
        try:
            result = job_processor.poll_and_claim_job(1)
            assert result is None
            mock_claim_api.assert_not_called()
        finally:
            job_processor._cpu_lock.release()

    def test_claim_job_skips_when_cpu_busy(self, mocker, mock_poll_deps):
        """
        Tests that if a CPU job is available but the CPU lock is held,
        the worker skips the claim.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = []
        mock_claim_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job'
        )
        mock_poll_api.return_value = [{'id': 5, 'render_device': 'CPU'}]

        job_processor._cpu_lock.acquire()
        try:
            result = job_processor.poll_and_claim_job(1)
            assert result is None
            mock_claim_api.assert_not_called()
        finally:
            job_processor._cpu_lock.release()

    def test_claim_job_skips_any_job_when_cpu_only_and_busy(
        self, mocker, mock_poll_deps
    ):
        """
        Tests that an 'ANY' device job is correctly skipped on a CPU-only
        worker if the CPU lock is busy.
        """
        mock_poll_api, mock_detect_gpu = mock_poll_deps
        mock_detect_gpu.return_value = []
        mock_claim_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job'
        )

        mock_poll_api.return_value = [{'id': 6, 'render_device': 'ANY'}]

        job_processor._cpu_lock.acquire()
        try:
            result = job_processor.poll_and_claim_job(1)
            assert result is None
            mock_claim_api.assert_not_called()
        finally:
            job_processor._cpu_lock.release()

    def test_poll_and_claim_job_returns_data_on_success(
        self, mocker, mock_poll_deps
    ):
        """
        Verifies that on a successful claim, the function returns the
        job data dictionary.
        """
        mock_poll_api, _ = mock_poll_deps
        mock_job = {'id': 1, 'name': 'Claim Me'}
        mock_poll_api.return_value = [mock_job]
        mock_claim_job_api = mocker.patch(
            'sethlans_worker_agent.api_handler.claim_job', return_value=True
        )

        result = job_processor.poll_and_claim_job(1)

        assert result is not None
        assert result['id'] == 1
        assert result['name'] == 'Claim Me'
        mock_claim_job_api.assert_called_once_with(1, 1)
