# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end tests for concurrency and database stability.
"""
import time
import requests
import threading
import uuid
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion, get_blender_process_count
from workers.constants import RenderSettings


class TestConcurrency(BaseE2ETest):
    """
    Validates the system's stability under high-concurrency scenarios,
    specifically to prevent database locking errors and to verify
    sequential CPU job processing.
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
            unique_id = uuid.uuid4().hex[:8]
            anim_payload = {
                "name": f"E2E Concurrency Test Animation {unique_id}",
                "project": self.project_id,
                "asset_id": self.anim_asset_id,
                "output_file_pattern": f"concurrent_anim_{unique_id}_####",
                "start_frame": 1,
                "end_frame": 2,
                "blender_version": self._blender_version_for_test,
                "render_settings": {
                    RenderSettings.SAMPLES: 8,
                    RenderSettings.RESOLUTION_X: 640,
                    RenderSettings.RESOLUTION_Y: 360,
                }
            }
            try:
                create_response = requests.post(
                    f"{MANAGER_URL}/animations/", json=anim_payload,
                )
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
            thread = threading.Thread(
                target=submit_animation, args=(animation_urls,),
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(animation_urls) == num_animations_to_submit, (
            "Not all animations were submitted successfully."
        )

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
        Verifies that the worker processes only one CPU job at a time by
        monitoring job statuses and running system processes during the render.
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
            "render_device": "CPU",
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
            "render_device": "CPU",
        }
        res2 = requests.post(f"{MANAGER_URL}/jobs/", json=job_payload_2)
        assert res2.status_code == 201
        job_url_2 = f"{MANAGER_URL}/jobs/{res2.json()['id']}/"

        # 2. Poll both jobs simultaneously, verifying state and process count.
        start_time = time.time()
        timeout_seconds = 360
        print("Starting live monitoring of jobs and processes...")

        while time.time() - start_time < timeout_seconds:
            proc_count = get_blender_process_count()
            assert proc_count <= 1, (
                f"Concurrency error! Found {proc_count} Blender processes "
                f"running simultaneously."
            )

            status1 = requests.get(job_url_1).json().get('status')
            status2 = requests.get(job_url_2).json().get('status')

            print(
                f"  [{int(time.time() - start_time)}s] "
                f"Job 1: {status1}, Job 2: {status2}, "
                f"Blender Procs: {proc_count}"
            )

            if status1 == 'RENDERING':
                assert status2 in ('QUEUED', 'DONE'), (
                    f"Concurrency error! Job 2 status is '{status2}' "
                    f"while Job 1 is rendering."
                )

            if status2 == 'RENDERING':
                assert status1 in ('QUEUED', 'DONE'), (
                    f"Concurrency error! Job 1 status is '{status1}' "
                    f"while Job 2 is rendering."
                )

            if status1 == 'DONE' and status2 == 'DONE':
                print("Both jobs completed successfully.")
                break

            time.sleep(2)
        else:
            self.fail(f"Test timed out after {timeout_seconds} seconds.")

        print("SUCCESS: Live monitoring confirmed sequential CPU job execution.")
