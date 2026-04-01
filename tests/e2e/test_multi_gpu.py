# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end tests for multi-GPU concurrent rendering and GPU split mode fallback.
"""
import re
import platform
import os
import time
import psutil
import pytest
import requests
import queue
import uuid
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import (
    poll_for_completion, is_gpu_available,
    is_self_hosted_runner, stop_worker_and_collect_logs,
)
from workers.constants import RenderSettings
from sethlans_worker_agent import system_monitor, hardware_detection


class TestMultiGpu(BaseE2ETest):
    """
    Validates GPU split mode behavior: concurrent rendering across multiple
    physical GPUs, and CPU fallback when all GPUs are saturated.
    """

    def _restart_worker_in_split_mode(self):
        """Stop the default worker and start a new one in GPU split mode."""
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)

        self.worker_log_queue = queue.Queue()
        env = {"SETHLANS_GPU_SPLIT_MODE": "true"}
        self.start_worker(self.worker_log_queue, extra_env=env)

    def _wait_for_all_jobs_claimed(self, job_urls, timeout=60):
        """Poll until none of the given job URLs have QUEUED status."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            statuses = [requests.get(url).json()['status'] for url in job_urls]
            if all(s != 'QUEUED' for s in statuses):
                print("All jobs have been claimed by the worker.")
                return
            time.sleep(2)
        pytest.fail("Not all jobs were claimed by the worker in time.")

    def test_multi_gpu_concurrent_rendering(self):
        """
        Verifies that a worker in GPU split mode can process multiple jobs in
        parallel on different physical GPUs.
        """
        is_standard_mac_ci = (
            platform.system() == "Darwin"
            and "CI" in os.environ
            and not is_self_hosted_runner()
        )
        if not is_gpu_available() or is_standard_mac_ci:
            pytest.skip(
                "Skipping multi-GPU test: No stable GPU or running in standard macOS CI."
            )

        hardware_detection._gpu_details_cache = None
        physical_gpus = system_monitor.get_gpu_device_details()
        num_gpus = len(physical_gpus)

        if num_gpus < 2:
            pytest.skip(
                f"Skipping multi-GPU test: Requires >= 2 physical GPUs, found {num_gpus}."
            )

        print(f"\n--- E2E TEST: Multi-GPU Concurrent Rendering for {num_gpus} GPUs ---")

        self._restart_worker_in_split_mode()

        # Submit one job per GPU
        job_urls = []
        for i in range(num_gpus):
            job_payload = {
                "name": f"E2E Multi-GPU Job {i}-{uuid.uuid4().hex[:8]}",
                "project": self.project_id,
                "asset_id": self.bmw_asset_id,
                "output_file_pattern": f"multi_gpu_job_{i}_####",
                "start_frame": 1, "end_frame": 1,
                "blender_version": self._blender_version_for_test,
                "render_device": "GPU",
                "render_settings": {RenderSettings.RESOLUTION_PERCENTAGE: 25},
            }
            create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
            assert create_response.status_code == 201, f"Failed to create job {i}"
            job_urls.append(f"{MANAGER_URL}/jobs/{create_response.json()['id']}/")

        # Poll for completion of ALL jobs
        print(f"Polling for completion of all {num_gpus} concurrent jobs...")
        for url in job_urls:
            poll_for_completion(url, timeout_seconds=360)

        print("All jobs completed. Verifying logs for concurrent assignment...")

        full_log = stop_worker_and_collect_logs(self)

        assignment_pattern = re.compile(r"Assigning to \[Physical GPU (\d+)]")
        assigned_indices = set(map(int, assignment_pattern.findall(full_log)))

        print(f"Detected assignments in log for GPU indices: {sorted(list(assigned_indices))}")
        assert len(assigned_indices) == num_gpus, (
            f"Expected jobs assigned to {num_gpus} unique GPUs, "
            f"but only found {len(assigned_indices)}."
        )

        print(f"SUCCESS: Verified concurrent job execution across all {num_gpus} GPUs.")

    def test_any_job_falls_back_to_cpu_in_split_mode(self):
        """
        Verifies that in GPU split mode, with all GPUs occupied, the worker will
        correctly fall back to using the CPU for a job with render_device='ANY'.
        """
        if not is_gpu_available() or psutil.cpu_count() <= 1:
            pytest.skip("Requires a multi-core, GPU-capable host.")

        hardware_detection._gpu_details_cache = None
        num_gpus = len(system_monitor.get_gpu_device_details())

        if num_gpus < 1:
            pytest.skip("Requires at least one GPU.")

        print(f"\n--- E2E TEST: 'ANY' Job CPU Fallback for {num_gpus} GPU(s) ---")

        self._restart_worker_in_split_mode()

        # Submit N GPU-only jobs to saturate all GPUs
        gpu_job_urls = self._submit_gpu_saturation_jobs(num_gpus)

        self._wait_for_all_jobs_claimed(gpu_job_urls)

        # Submit one 'ANY' device job that should fall back to CPU
        cpu_job_url, cpu_job_id = self._submit_cpu_fallback_job()

        # Verify the CPU job enters RENDERING state
        self._assert_job_reaches_rendering(cpu_job_url)

        # Poll for completion of all jobs
        print("Polling for completion of all jobs...")
        for url in gpu_job_urls + [cpu_job_url]:
            poll_for_completion(url, timeout_seconds=360)

        print("All jobs completed. Verifying logs for correct CPU fallback...")

        full_log = stop_worker_and_collect_logs(self)
        self._verify_cpu_fallback_logs(full_log, cpu_job_id)

        print("SUCCESS: Log verification confirmed correct CPU fallback behavior.")

    def _submit_gpu_saturation_jobs(self, num_gpus):
        """Submit GPU-only jobs to saturate all available GPUs."""
        gpu_job_urls = []
        print(f"Submitting {num_gpus} GPU-only jobs to saturate GPUs...")
        for i in range(num_gpus):
            job_payload = {
                "name": f"E2E GPU Saturation Job {i}-{uuid.uuid4().hex[:8]}",
                "project": self.project_id,
                "asset_id": self.bmw_asset_id,
                "output_file_pattern": f"gpu_saturate_{i}_####",
                "start_frame": 1, "end_frame": 1,
                "blender_version": self._blender_version_for_test,
                "render_device": "GPU",
                "render_settings": {RenderSettings.RESOLUTION_PERCENTAGE: 25},
            }
            res = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
            assert res.status_code == 201
            gpu_job_urls.append(f"{MANAGER_URL}/jobs/{res.json()['id']}/")
        return gpu_job_urls

    def _submit_cpu_fallback_job(self):
        """Submit a single 'ANY' device job expected to fall back to CPU."""
        print("Submitting one 'ANY' device job for CPU fallback...")
        payload = {
            "name": f"E2E CPU Fallback Job {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "cpu_fallback_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "ANY",
            "render_settings": {RenderSettings.SAMPLES: 10},
        }
        res = requests.post(f"{MANAGER_URL}/jobs/", json=payload)
        assert res.status_code == 201
        job_id = res.json()['id']
        return f"{MANAGER_URL}/jobs/{job_id}/", job_id

    def _assert_job_reaches_rendering(self, job_url, timeout=30):
        """Assert that a job enters RENDERING state within the timeout."""
        print("Verifying that the fallback job was claimed by the CPU...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = requests.get(job_url).json().get('status')
            if status == 'RENDERING':
                print("CPU fallback job is in RENDERING state as expected.")
                return
            time.sleep(2)
        pytest.fail("CPU fallback job did not enter RENDERING state in time.")

    def _verify_cpu_fallback_logs(self, full_log, cpu_job_id):
        """Verify worker logs confirm the CPU fallback path was taken."""
        key_pattern = re.compile(
            rf".*\[Job {cpu_job_id}\] \[CPU Fallback\] Forcing CPU configuration.*"
        )
        match = key_pattern.search(full_log)
        if match:
            print(
                f"\n[VERIFICATION] Found key log message in worker output:"
                f"\n  >> {match.group(0).strip()}\n"
            )

        fallback_log = re.compile(
            rf"\[Job {cpu_job_id}\] \[CPU Fallback\] "
            rf"Forcing CPU configuration for 'ANY' job"
        )
        cpu_config_log = re.compile(
            rf"\[Job {cpu_job_id}\].*Configuring job for CPU rendering"
        )
        gpu_config_log = re.compile(
            rf"\[Job {cpu_job_id}\].*Configuring job for GPU rendering"
        )

        assert fallback_log.search(full_log), \
            "Log did not contain the CPU fallback message."
        assert cpu_config_log.search(full_log), \
            "Log did not contain the CPU configuration message."
        assert not gpu_config_log.search(full_log), \
            "Log incorrectly contained a GPU configuration message."
