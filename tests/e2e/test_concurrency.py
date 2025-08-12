# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/2/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
"""
End-to-end tests for concurrency, database stability, and multi-GPU rendering. 🏎️💨
"""
import queue
import re
import platform
import os
import time
import psutil
import pytest
import requests
import threading
import uuid
from datetime import datetime
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, is_gpu_available, get_blender_process_count, is_self_hosted_runner
from workers.constants import RenderSettings
from sethlans_worker_agent import system_monitor


class TestConcurrency(BaseE2ETest):
    """
    Validates the system's stability under high-concurrency scenarios,
    specifically to prevent database locking errors and to verify multi-GPU
    job processing.
    """

    def test_high_concurrency_animation_submission(self):
        """
        Tests system stability by submitting multiple animation jobs in quick
        succession to simulate a high-load scenario that could cause
        database lock contention. This serves as a regression test for the
        'database is locked' SQLite error.
        """
        print("\n--- E2E TEST: High-Concurrency Animation Submission ---")

        num_animations_to_submit = 3
        animation_urls = []
        threads = []

        def submit_animation(url_list):
            """Target function for each thread to submit an animation job."""
            # Use a short UUID to guarantee a unique name for each concurrent request
            unique_id = uuid.uuid4().hex[:8]
            anim_payload = {
                "name": f"E2E Concurrency Test Animation {unique_id}",
                "project": self.project_id,
                "asset_id": self.anim_asset_id,
                "output_file_pattern": f"concurrent_anim_{unique_id}_####",
                "start_frame": 1,
                "end_frame": 2,  # Short animation
                "blender_version": self._blender_version_for_test,
                "render_settings": {
                    RenderSettings.SAMPLES: 8,
                    RenderSettings.RESOLUTION_X: 640,
                    RenderSettings.RESOLUTION_Y: 360,
                }
            }
            try:
                create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
                create_response.raise_for_status()
                anim_id = create_response.json()['id']
                anim_url = f"{MANAGER_URL}/animations/{anim_id}/"
                url_list.append(anim_url)
                print(f"  Successfully submitted animation: {anim_url}")
            except requests.RequestException as e:
                print(f"  Error submitting animation: {e}")

        # Launch threads to submit animations concurrently
        print(f"Submitting {num_animations_to_submit} animations concurrently...")
        for _ in range(num_animations_to_submit):
            thread = threading.Thread(target=submit_animation, args=(animation_urls,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(animation_urls) == num_animations_to_submit, "Not all animations were submitted successfully."

        # Poll for completion of all submitted animations
        print("\nPolling for completion of all concurrent animations...")
        for anim_url in animation_urls:
            try:
                poll_for_completion(anim_url, timeout_seconds=180)
                print(f"  Animation {anim_url} completed successfully.")
            except Exception as e:
                self.fail(f"Polling failed for animation {anim_url}: {e}")

        print("\nSUCCESS: All concurrent animation jobs completed without database errors.")

    def test_single_cpu_job_concurrency(self):
        """
        Verifies that the worker processes only one CPU job at a time by monitoring
        job statuses and running system processes during the render.
        """
        print("\n--- E2E TEST: Single CPU Job Concurrency (Live Monitoring) ---")

        # 1. Submit two CPU-only jobs.
        job_payload_1 = {
            "name": f"E2E CPU Concurrency Job 1 {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "cpu_concurrency_1_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        res1 = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload_1)
        assert res1.status_code == 201
        job_url_1 = f"{MANAGER_URL}/jobs/{res1.json()['id']}/"

        job_payload_2 = {
            "name": f"E2E CPU Concurrency Job 2 {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,
            "output_file_pattern": "cpu_concurrency_2_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "CPU"
        }
        res2 = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload_2)
        assert res2.status_code == 201
        job_url_2 = f"{MANAGER_URL}/jobs/{res2.json()['id']}/"

        # 2. Poll both jobs simultaneously, verifying state and process count.
        start_time = time.time()
        timeout_seconds = 360
        print("Starting live monitoring of jobs and processes...")

        while time.time() - start_time < timeout_seconds:
            # A. Check running Blender processes
            proc_count = get_blender_process_count()
            assert proc_count <= 1, f"Concurrency error! Found {proc_count} Blender processes running simultaneously."

            # B. Check job statuses from the API
            status1 = requests.get(job_url_1).json().get('status')
            status2 = requests.get(job_url_2).json().get('status')

            print(
                f"  [{int(time.time() - start_time)}s] Job 1: {status1}, Job 2: {status2}, Blender Procs: {proc_count}")

            # C. Assert that one job waits while the other renders
            if status1 == 'RENDERING':
                assert status2 in ('QUEUED', 'DONE'), \
                    f"Concurrency error! Job 2 status is '{status2}' while Job 1 is rendering."

            if status2 == 'RENDERING':
                assert status1 in ('QUEUED', 'DONE'), \
                    f"Concurrency error! Job 1 status is '{status1}' while Job 2 is rendering."

            # D. Exit condition
            if status1 == 'DONE' and status2 == 'DONE':
                print("Both jobs completed successfully.")
                break

            time.sleep(2)
        else:
            self.fail(f"Test timed out after {timeout_seconds} seconds.")

        print("SUCCESS: Live monitoring confirmed sequential CPU job execution.")

    def test_multi_gpu_concurrent_rendering(self):
        """
        Verifies that a worker in GPU split mode can process multiple jobs in
        parallel on different physical GPUs.
        """
        is_standard_mac_ci = platform.system() == "Darwin" and "CI" in os.environ and not is_self_hosted_runner()
        if not is_gpu_available() or is_standard_mac_ci:
            pytest.skip("Skipping multi-GPU test: No stable GPU or running in standard macOS CI.")

        system_monitor._gpu_details_cache = None
        physical_gpus = system_monitor.get_gpu_device_details()
        num_gpus = len(physical_gpus)

        if num_gpus < 2:
            pytest.skip(f"Skipping multi-GPU test: Requires at least 2 physical GPUs, found {num_gpus}.")

        print(f"\n--- E2E TEST: Multi-GPU Concurrent Rendering for {num_gpus} GPUs ---")

        # 1. Stop default worker and start one in split mode
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)

        self.worker_log_queue = queue.Queue()
        env = {"SETHLANS_GPU_SPLIT_MODE": "true"}
        self.start_worker(self.worker_log_queue, extra_env=env)

        # 2. Submit one job per GPU
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
                "render_settings": {RenderSettings.RESOLUTION_PERCENTAGE: 25}
            }
            create_response = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
            assert create_response.status_code == 201, f"Failed to create job {i}"
            job_urls.append(f"{MANAGER_URL}/jobs/{create_response.json()['id']}/")

        # 3. Poll for completion of ALL jobs
        print(f"Polling for completion of all {num_gpus} concurrent jobs...")
        for url in job_urls:
            poll_for_completion(url, timeout_seconds=360)

        print("All jobs completed. Verifying logs for concurrent assignment...")

        # 4. Verify logs
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)
        if self.worker_log_thread and self.worker_log_thread.is_alive():
            self.worker_log_thread.join(timeout=5)

        worker_logs = []
        while not self.worker_log_queue.empty():
            worker_logs.append(self.worker_log_queue.get_nowait())
        full_log = "".join(worker_logs)

        # Find all unique GPU assignment indices from the logs
        assignment_pattern = re.compile(r"Assigning to \[Physical GPU (\d+)]")
        assigned_indices = set(map(int, assignment_pattern.findall(full_log)))

        print(f"Detected assignments in log for GPU indices: {sorted(list(assigned_indices))}")
        assert len(assigned_indices) == num_gpus, \
            f"Expected jobs to be assigned to {num_gpus} unique GPUs, but only found {len(assigned_indices)}."

        print(f"SUCCESS: Verified concurrent job execution across all {num_gpus} GPUs.")

    def test_any_job_falls_back_to_cpu_in_split_mode(self):
        """
        Verifies that in GPU split mode, with all GPUs occupied, the worker will
        correctly fall back to using the CPU for a job with render_device='ANY'.
        """
        if not is_gpu_available() or psutil.cpu_count() <= 1:
            pytest.skip("Requires a multi-core, GPU-capable host.")

        system_monitor._gpu_details_cache = None
        physical_gpus = system_monitor.get_gpu_device_details()
        num_gpus = len(physical_gpus)

        if num_gpus < 1:
            pytest.skip("Requires at least one GPU.")

        print(f"\n--- E2E TEST: 'ANY' Job CPU Fallback for {num_gpus} GPU(s) ---")

        # 1. Stop default worker and start one in split mode
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)

        self.worker_log_queue = queue.Queue()
        env = {"SETHLANS_GPU_SPLIT_MODE": "true"}
        self.start_worker(self.worker_log_queue, extra_env=env)

        # 2. Submit N GPU-only jobs to saturate all GPUs
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
                "render_settings": {RenderSettings.RESOLUTION_PERCENTAGE: 25}
            }
            res = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload)
            assert res.status_code == 201
            gpu_job_urls.append(f"{MANAGER_URL}/jobs/{res.json()['id']}/")

        # 3. Wait until all GPU jobs are confirmed to be claimed (not queued).
        print("Waiting for worker to claim all GPU saturation jobs...")
        start_time = time.time()
        while time.time() - start_time < 60:
            statuses = [requests.get(url).json()['status'] for url in gpu_job_urls]
            if all(s != 'QUEUED' for s in statuses):
                print("All GPU saturation jobs have been claimed by the worker.")
                break
            time.sleep(2)
        else:
            pytest.fail("Not all GPU saturation jobs were claimed by the worker in time.")

        # 4. Submit one 'ANY' device job that should fall back to CPU
        print("Submitting one 'ANY' device job for CPU fallback...")
        cpu_fallback_payload = {
            "name": f"E2E CPU Fallback Job {uuid.uuid4().hex[:8]}",
            "project": self.project_id,
            "asset_id": self.scene_asset_id,  # Use a lighter scene for CPU
            "output_file_pattern": "cpu_fallback_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "render_device": "ANY",
            "render_settings": {RenderSettings.SAMPLES: 10}
        }
        res = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_fallback_payload)
        assert res.status_code == 201
        cpu_job_url = f"{MANAGER_URL}/jobs/{res.json()['id']}/"

        # 5. Wait a moment and verify the CPU job is also rendering
        print("Verifying that the fallback job was claimed by the CPU...")
        start_time = time.time()
        cpu_job_is_rendering = False
        while time.time() - start_time < 30:
            status_res = requests.get(cpu_job_url)
            assert status_res.status_code == 200
            current_status = status_res.json().get('status')
            if current_status == 'RENDERING':
                cpu_job_is_rendering = True
                break
            time.sleep(2)

        assert cpu_job_is_rendering, "CPU fallback job did not enter RENDERING state in time."
        print("CPU fallback job is in RENDERING state as expected.")

        # 6. Poll for completion of all jobs to ensure they all finish
        print("Polling for completion of all jobs...")
        all_urls = gpu_job_urls + [cpu_job_url]
        for url in all_urls:
            poll_for_completion(url, timeout_seconds=360)

        print("All jobs completed. Verifying logs for correct CPU fallback configuration...")

        # 7. Stop the worker and verify its logs
        if self.worker_process and self.worker_process.poll() is None:
            self.worker_process.kill()
            self.worker_process.wait(timeout=10)
        if self.worker_log_thread and self.worker_log_thread.is_alive():
            self.worker_log_thread.join(timeout=5)

        worker_logs = []
        while not self.worker_log_queue.empty():
            worker_logs.append(self.worker_log_queue.get_nowait())
        full_log = "".join(worker_logs)

        cpu_job_id = res.json()['id']

        # --- NEW: Explicitly print the key log message for verification ---
        key_log_pattern = re.compile(
            rf".*\[Job {cpu_job_id}\] \[CPU Fallback\] Forcing CPU configuration.*"
        )
        match = key_log_pattern.search(full_log)
        if match:
            print(f"\n[VERIFICATION] Found key log message in worker output:\n  >> {match.group(0).strip()}\n")
        # --- END NEW SECTION ---

        # Assertions to confirm the correct logic path was taken
        fallback_log_pattern = re.compile(
            rf"\[Job {cpu_job_id}\] \[CPU Fallback\] Forcing CPU configuration for 'ANY' job"
        )
        cpu_config_log_pattern = re.compile(rf"\[Job {cpu_job_id}\].*Configuring job for CPU rendering")
        gpu_config_log_pattern = re.compile(rf"\[Job {cpu_job_id}\].*Configuring job for GPU rendering")

        assert fallback_log_pattern.search(full_log), "Log did not contain the CPU fallback message."
        assert cpu_config_log_pattern.search(full_log), "Log did not contain the CPU configuration message."
        assert not gpu_config_log_pattern.search(full_log), "Log incorrectly contained a GPU configuration message."

        print("SUCCESS: Log verification confirmed correct CPU fallback behavior.")