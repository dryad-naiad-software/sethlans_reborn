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
# Created by Mario Estrella on 7/31/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/test_worker_behavior.py

import os
import platform
import time

import pytest
import requests

from sethlans_worker_agent import system_monitor
from workers.constants import RenderDevice, RenderSettings
from workers.models.jobs import Job
from .shared_setup import BaseE2ETest, MANAGER_URL
from .utils import is_gpu_available

JobStatus = Job.status


class TestWorkerRegistration(BaseE2ETest):
    """
    Tests related to worker registration and hardware reporting.
    """

    def test_worker_reports_correct_gpu_devices(self):
        print("\n--- ACTION: Verifying worker hardware reporting ---")
        print("Detecting local GPU devices for comparison...")
        local_devices = system_monitor.detect_gpu_devices()
        print(f"Locally detected devices: {local_devices}")

        print("Querying API for worker's reported data...")
        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        worker_data = response.json()[0]
        reported_devices = worker_data.get('supported_gpu_devices', [])
        print(f"Worker reported devices: {reported_devices}")

        assert set(local_devices) == set(reported_devices)
        print("SUCCESS: Worker correctly reported its GPU capabilities.")


class TestJobFiltering(BaseE2ETest):
    """
    Tests that workers correctly filter jobs based on their capabilities.
    """

    @classmethod
    def setup_class(cls):
        """
        Sets up the class by mocking a CPU-only worker environment.
        """
        os.environ["SETHLANS_MOCK_CPU_ONLY"] = "true"
        super().setup_class()

    def test_cpu_worker_ignores_gpu_job(self):
        print("\n--- ACTION: Testing that a CPU-only worker ignores GPU-only jobs ---")

        # FIX: All job payloads must now include start_frame and end_frame.
        gpu_job_payload = {
            "name": "GPU-Only Job", "project": self.project_id, "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.GPU, "output_file_pattern": "gpu_filter_test_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 8}
        }
        cpu_job_payload = {
            "name": "CPU-Only Job", "project": self.project_id, "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.CPU, "output_file_pattern": "cpu_filter_test_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 8}
        }

        gpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_job_payload)
        gpu_response.raise_for_status()
        gpu_job_id = gpu_response.json()['id']

        cpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_job_payload)
        cpu_response.raise_for_status()
        cpu_job_id = cpu_response.json()['id']

        for _ in range(15):
            cpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']
            if cpu_job_status != JobStatus.QUEUED:
                break
            time.sleep(2)

        final_gpu_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
        final_cpu_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']

        assert final_gpu_status == JobStatus.QUEUED
        assert final_cpu_status != JobStatus.QUEUED
        print("SUCCESS: CPU-only worker correctly ignored the GPU job and processed the CPU job.")


class TestProjectPauseWorkflow(BaseE2ETest):

    def test_worker_respects_paused_project(self):
        print("\n--- ACTION: Testing worker respects paused projects ---")

        paused_project_payload = {"name": f"E2E-Paused-Project-{int(time.time())}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=paused_project_payload)
        response.raise_for_status()
        paused_project_id = response.json()['id']

        paused_asset_id = self._upload_test_asset(
            f"Paused Project Asset {paused_project_id}", self.test_blend_file, paused_project_id
        )

        # FIX: Payloads updated from 'frame_number' to 'start_frame' and 'end_frame'.
        active_job_payload = {
            "name": "Active Project Job", "project": self.project_id, "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.ANY, "output_file_pattern": "active_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 1}
        }
        paused_job_payload = {
            "name": "Paused Project Job", "project": paused_project_id, "asset_id": paused_asset_id,
            "render_device": RenderDevice.ANY, "output_file_pattern": "paused_####",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 1}
        }

        active_res = requests.post(f"{MANAGER_URL}/jobs/", json=active_job_payload)
        active_res.raise_for_status()
        active_job_id = active_res.json()['id']

        paused_res = requests.post(f"{MANAGER_URL}/jobs/", json=paused_job_payload)
        paused_res.raise_for_status()
        paused_job_id = paused_res.json()['id']

        pause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/pause/")
        pause_res.raise_for_status()

        for _ in range(15):
            active_job_status = requests.get(f"{MANAGER_URL}/jobs/{active_job_id}/").json()['status']
            if active_job_status == JobStatus.DONE:
                break
            time.sleep(2)

        assert requests.get(f"{MANAGER_URL}/jobs/{active_job_id}/").json()['status'] == JobStatus.DONE
        assert requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status'] == JobStatus.QUEUED

        unpause_res = requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/unpause/")
        unpause_res.raise_for_status()

        for _ in range(15):
            paused_job_status = requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status']
            if paused_job_status == JobStatus.DONE:
                break
            time.sleep(2)
        assert requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status'] == JobStatus.DONE


class TestForcedHardwareModes(BaseE2ETest):

    def test_force_cpu_only_mode(self):
        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_CPU_ONLY": "true"})

        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        response.raise_for_status()
        worker_data = response.json()[0]
        assert worker_data.get('supported_gpu_devices') == []
        print("SUCCESS: Worker in CPU-only mode correctly reported no GPU devices.")

    def test_force_gpu_only_mode(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        if not is_gpu_available() or (platform.system() == "Darwin" and is_ci):
            pytest.skip("Skipping GPU test: No GPU or in unstable macOS CI.")

        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_GPU_ONLY": "true"})

        # FIX: Add required frame range.
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "GPU Job", "project": self.project_id, "asset_id": self.scene_asset_id, "render_device": "GPU",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "force_gpu_####"
        })
        gpu_res.raise_for_status()

        # FIX: Add required frame range.
        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "CPU Job", "project": self.project_id, "asset_id": self.scene_asset_id, "render_device": "CPU",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "force_cpu_####"
        })
        cpu_res.raise_for_status()

        gpu_job_id = gpu_res.json()['id']
        cpu_job_id = cpu_res.json()['id']

        for _ in range(15):
            gpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
            if gpu_job_status != JobStatus.QUEUED:
                break
            time.sleep(2)

        final_gpu_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
        final_cpu_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']

        assert final_gpu_status != JobStatus.QUEUED
        assert final_cpu_status == JobStatus.QUEUED


class TestDefaultWorkerFlexibility(BaseE2ETest):

    def test_gpu_capable_worker_handles_cpu_and_gpu_jobs(self):
        if not is_gpu_available() or (platform.system() == "Darwin" and os.environ.get("CI") == "true"):
            pytest.skip("Requires a stable GPU-capable host.")

        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue)

        # FIX: Add required frame range.
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "Flex GPU Job", "project": self.project_id, "asset_id": self.scene_asset_id, "render_device": "GPU",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "flex_gpu_####"
        })
        gpu_res.raise_for_status()
        gpu_job_id = gpu_res.json()['id']

        # FIX: Add required frame range.
        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "Flex CPU Job", "project": self.project_id, "asset_id": self.scene_asset_id, "render_device": "CPU",
            "start_frame": 1, "end_frame": 1,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "flex_cpu_####"
        })
        cpu_res.raise_for_status()
        cpu_job_id = cpu_res.json()['id']

        for _ in range(30):
            gpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
            cpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']
            if gpu_job_status == JobStatus.DONE and cpu_job_status == JobStatus.DONE:
                break
            time.sleep(2)

        final_gpu_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
        final_cpu_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']

        assert final_gpu_status == JobStatus.DONE
        assert final_cpu_status == JobStatus.DONE