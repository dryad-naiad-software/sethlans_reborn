#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
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
import pytest
import requests
import threading
import uuid
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, is_gpu_available
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


    def test_multi_gpu_concurrent_rendering(self):
        """
        Verifies that a worker in GPU split mode can process multiple jobs in
        parallel on different physical GPUs.
        """
        if not is_gpu_available() or (platform.system() == "Darwin" and "CI" in os.environ):
            pytest.skip("Skipping multi-GPU test: No stable GPU or running in macOS CI.")

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