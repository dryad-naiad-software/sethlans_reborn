# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_worker_behavior.py
"""
End-to-end tests for core worker agent behavior and logic.
"""

import os
import platform
import queue
import re
import psutil
import pytest
import requests
import uuid
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import is_gpu_available, poll_for_completion, is_self_hosted_runner
from sethlans_worker_agent import system_monitor


class TestWorkerBehavior(BaseE2ETest):
    """
    Validates worker registration, hardware reporting, and job handling flexibility.
    """

    def test_worker_reports_correct_gpu_devices(self):
        """
        Verifies that a worker correctly detects and reports its available GPUs.
        """
        if not is_gpu_available():
            pytest.skip("Requires a GPU-capable host for a meaningful comparison.")

        print("\n--- E2E TEST: Worker GPU Hardware Reporting ---")

        # Get the ground truth by running the detection logic directly in the test
        print("Detecting local GPU devices for comparison...")
        local_devices = system_monitor.detect_gpu_devices()
        print(f"Locally detected devices: {local_devices}")

        print("Querying API for worker's reported data...")
        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        # Assuming only one worker is running in the test environment
        assert len(response.json()) > 0, "No worker has registered with the manager."
        worker_data = response.json()[0]

        reported_devices = worker_data.get('available_tools', {}).get('gpu_devices', [])
        print(f"Worker reported devices via API: {reported_devices}")

        assert set(local_devices) == set(reported_devices), "Worker's reported GPUs do not match locally detected GPUs."
        print("SUCCESS: Worker correctly reported its GPU capabilities.")

    def test_gpu_capable_worker_handles_cpu_and_gpu_jobs(self):
        """
        Verifies that a default (non-forced) GPU-capable worker can process
        both CPU-only and GPU-only jobs.
        """
        is_standard_mac_ci = platform.system() == "Darwin" and "CI" in os.environ and not is_self_hosted_runner()
        if not is_gpu_available() or is_standard_mac_ci:
            pytest.skip("Requires a stable GPU-capable host.")

        print("\n--- E2E TEST: Default Worker Job Flexibility ---")

        # Submit a GPU-only job
        gpu_payload = {
            "name": f"E2E Worker-Flex GPU Job {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "flex_gpu_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "GPU"
        }
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_payload)
        assert gpu_res.status_code == 201
        gpu_job_url = f"{MANAGER_URL}/jobs/{gpu_res.json()['id']}/"
        print(f"Submitted GPU-only job: {gpu_job_url}")

        # Submit a CPU-only job
        cpu_payload = {
            "name": f"E2E Worker-Flex CPU Job {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "flex_cpu_####",
            "start_frame": 1,
            "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_payload)
        assert cpu_res.status_code == 201
        cpu_job_url = f"{MANAGER_URL}/jobs/{cpu_res.json()['id']}/"
        print(f"Submitted CPU-only job: {cpu_job_url}")

        print("Waiting for both jobs to complete...")
        final_gpu_data = poll_for_completion(gpu_job_url, timeout_seconds=180)
        final_cpu_data = poll_for_completion(cpu_job_url, timeout_seconds=180)

        assert final_gpu_data['status'] == 'DONE'
        assert final_cpu_data['status'] == 'DONE'
        print("SUCCESS: Default GPU-capable worker successfully completed both CPU and GPU jobs.")

    def test_automatic_thread_reduction_in_mixed_mode(self):
        """
        Verifies that a worker in default mixed-mode on a multi-core, GPU-capable
        machine automatically reserves threads for GPU tasks when running a CPU job.
        """
        if not is_gpu_available() or psutil.cpu_count() <= 1:
            pytest.skip("Requires a multi-core, GPU-capable host.")

        print("\n--- E2E TEST: Automatic CPU Thread Reservation ---")

        # 1. Stop default worker and start a fresh one to capture its specific logs
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)

        log_queue = queue.Queue()
        # Start with no extra env vars to ensure default mixed-mode operation
        self.start_worker(log_queue)

        # 2. Submit a CPU job
        job_payload = {
            "name": f"E2E Auto-Threads Test {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "e2e_auto_threads_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU",
        }
        create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
        assert create_response.status_code == 201
        job_url = f"{MANAGER_URL}/jobs/{create_response.json()['id']}/"

        # 3. Wait for completion
        poll_for_completion(job_url)

        # 4. Stop worker and get its logs
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)
        if self.worker_log_thread and self.worker_log_thread.is_alive():
            self.worker_log_thread.join(timeout=5)

        worker_logs = []
        while not log_queue.empty():
            worker_logs.append(log_queue.get_nowait())
        full_log = "".join(worker_logs)

        # 5. Verify the logs
        expected_log_msg = "Applying automatic CPU thread limit for mixed-mode operation"
        assert expected_log_msg in full_log, "Automatic thread limit log message not found."

        # Find the command line and verify the --threads flag was used
        command_match = re.search(r"Running Command: .* --threads \d+ .*", full_log)
        assert command_match is not None, "The '--threads' flag was not found in the worker's command log."

        print("SUCCESS: Worker correctly applied automatic thread reservation.")