# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_hardware_modes.py
"""
End-to-end tests for forced hardware modes and job filtering. ⚙️
"""
import time
import platform
import os
import pytest
import requests
import uuid

from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import is_gpu_available, poll_for_completion, is_self_hosted_runner


class TestHardwareModes(BaseE2ETest):
    """
    Validates worker behavior when started with forced hardware flags and
    the corresponding job filtering on the manager.
    """

    def test_force_cpu_only_mode_reports_no_gpus(self):
        """
        Verifies that a worker started with FORCE_CPU_ONLY reports no GPUs,
        even if the host machine has them.
        """
        print("\n--- E2E TEST: Forced CPU Mode (Hardware Report) ---")
        # Stop the default worker and start one in forced CPU mode
        if self.worker_process:
            self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_CPU_ONLY": "true"})

        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        assert len(response.json()) > 0
        worker_data = response.json()[0]

        reported_gpus = worker_data.get('available_tools', {}).get('gpu_devices', [])
        assert reported_gpus == []
        print("SUCCESS: Worker in FORCE_CPU_ONLY mode correctly reported no GPU devices.")

    def test_force_cpu_only_worker_ignores_gpu_job(self):
        """
        Verifies that a worker in FORCE_CPU_ONLY mode will process a CPU
        job while leaving a GPU-only job in the queue.
        """
        print("\n--- E2E TEST: Forced CPU Mode (Job Filtering) ---")
        # Stop the default worker and start one in forced CPU mode
        if self.worker_process:
            self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_CPU_ONLY": "true"})

        gpu_payload = {
            "name": f"E2E GPU-Only Job (ignored) {uuid.uuid4().hex[:8]}", "project": self.project_id,
            "asset_id": self.scene_asset_id, "output_file_pattern": "ignored_gpu_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_device": "GPU"
        }
        cpu_payload = {
            "name": f"E2E CPU-Only Job (processed) {uuid.uuid4().hex[:8]}", "project": self.project_id,
            "asset_id": self.scene_asset_id, "output_file_pattern": "processed_cpu_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_payload)
        assert gpu_res.status_code == 201
        gpu_job_url = f"{MANAGER_URL}/jobs/{gpu_res.json()['id']}/"

        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_payload)
        assert cpu_res.status_code == 201
        cpu_job_url = f"{MANAGER_URL}/jobs/{cpu_res.json()['id']}/"

        print("Polling for CPU job completion...")
        poll_for_completion(cpu_job_url)

        final_gpu_status = requests.get(gpu_job_url).json()['status']
        assert final_gpu_status == 'QUEUED'
        print("SUCCESS: Forced CPU worker correctly ignored the GPU job and processed the CPU job.")

    def test_force_gpu_only_worker_ignores_cpu_job(self):
        """
        Verifies that a worker in FORCE_GPU_ONLY mode will process a GPU
        job while leaving a CPU-only job in the queue.
        """
        is_standard_mac_ci = platform.system() == "Darwin" and "CI" in os.environ and not is_self_hosted_runner()
        if not is_gpu_available() or is_standard_mac_ci:
            pytest.skip("Requires a stable GPU-capable host.")

        print("\n--- E2E TEST: Forced GPU Mode (Job Filtering) ---")
        # Stop the default worker and start one in forced GPU mode
        if self.worker_process:
            self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_GPU_ONLY": "true"})

        gpu_payload = {
            "name": f"E2E GPU-Only Job (processed) {uuid.uuid4().hex[:8]}", "project": self.project_id,
            "asset_id": self.scene_asset_id, "output_file_pattern": "processed_gpu_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_device": "GPU"
        }
        cpu_payload = {
            "name": f"E2E CPU-Only Job (ignored) {uuid.uuid4().hex[:8]}", "project": self.project_id,
            "asset_id": self.scene_asset_id, "output_file_pattern": "ignored_cpu_####",
            "start_frame": 1, "end_frame": 1, "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_payload)
        assert gpu_res.status_code == 201
        gpu_job_url = f"{MANAGER_URL}/jobs/{gpu_res.json()['id']}/"

        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_payload)
        assert cpu_res.status_code == 201
        cpu_job_url = f"{MANAGER_URL}/jobs/{cpu_res.json()['id']}/"

        print("Polling for GPU job completion...")
        poll_for_completion(gpu_job_url)

        final_cpu_status = requests.get(cpu_job_url).json()['status']
        assert final_cpu_status == 'QUEUED'
        print("SUCCESS: Forced GPU worker correctly ignored the CPU job and processed the GPU job.")