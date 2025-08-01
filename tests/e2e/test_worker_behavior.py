# tests/e2e/test_worker_behavior.py

import os
import platform
import socket
import time

import pytest
import requests

from sethlans_worker_agent import system_monitor
from workers.constants import RenderSettings, RenderDevice
from sethlans_worker_agent import config as worker_config
from .shared_setup import BaseE2ETest, MANAGER_URL
from .utils import is_gpu_available


class TestWorkerRegistration(BaseE2ETest):
    def test_worker_reports_correct_gpu_devices(self):
        print("\n--- ACTION: Verifying worker hardware reporting ---")
        print("Detecting local GPU devices for comparison...")
        expected_gpus = system_monitor.detect_gpu_devices()
        print(f"Locally detected devices: {expected_gpus}")

        print("Querying API for worker's reported data...")
        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        assert response.status_code == 200
        workers_data = response.json()
        assert len(workers_data) > 0

        local_hostname = socket.gethostname()
        worker_record = next((w for w in workers_data if w['hostname'] == local_hostname), None)
        assert worker_record is not None

        reported_tools = worker_record.get('available_tools', {})
        reported_gpus = reported_tools.get('gpu_devices', [])
        print(f"Worker reported devices: {reported_gpus}")

        assert sorted(reported_gpus) == sorted(expected_gpus)
        print("SUCCESS: Worker correctly reported its GPU capabilities.")


class TestJobFiltering(BaseE2ETest):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        os.environ["SETHLANS_MOCK_CPU_ONLY"] = "true"

    @classmethod
    def teardown_class(cls):
        if "SETHLANS_MOCK_CPU_ONLY" in os.environ:
            del os.environ["SETHLANS_MOCK_CPU_ONLY"]
        super().teardown_class()

    def test_cpu_worker_ignores_gpu_job(self):
        print("\n--- ACTION: Testing that a CPU-only worker ignores GPU-only jobs ---")

        gpu_job_payload = {
            "name": "GPU-Only Job", "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.GPU, "output_file_pattern": "gpu_filter_test_####",
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 8}
        }
        cpu_job_payload = {
            "name": "CPU-Only Job", "asset_id": self.scene_asset_id,
            "render_device": RenderDevice.CPU, "output_file_pattern": "cpu_filter_test_####",
            "blender_version": self._blender_version_for_test, "render_settings": {RenderSettings.SAMPLES: 8}
        }

        gpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=gpu_job_payload)
        gpu_response.raise_for_status()
        cpu_response = requests.post(f"{MANAGER_URL}/jobs/", json=cpu_job_payload)
        cpu_response.raise_for_status()

        gpu_job_id = gpu_response.json()['id']
        cpu_job_id = cpu_response.json()['id']

        cpu_job_completed = False
        for _ in range(30):
            cpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{cpu_job_id}/").json()['status']
            gpu_job_status = requests.get(f"{MANAGER_URL}/jobs/{gpu_job_id}/").json()['status']
            assert gpu_job_status == "QUEUED"
            if cpu_job_status == "DONE":
                cpu_job_completed = True
                break
            time.sleep(2)

        assert cpu_job_completed, "CPU job was not completed by the worker."
        print("SUCCESS: CPU-only worker correctly ignored the GPU job and processed the CPU job.")


class TestProjectPauseWorkflow(BaseE2ETest):
    def test_worker_respects_paused_project(self):
        print("\n--- ACTION: Testing worker respects paused projects ---")

        paused_project_payload = {"name": f"E2E-Paused-Project-{int(time.time())}"}
        response = requests.post(f"{MANAGER_URL}/projects/", json=paused_project_payload)
        response.raise_for_status()
        paused_project_id = response.json()['id']

        paused_asset_id = self._upload_test_asset(
            "Paused Project Asset", worker_config.TEST_BLEND_FILE_PATH, paused_project_id
        )

        active_job_payload = {
            "name": "Active Project Job", "asset_id": self.scene_asset_id,
            "output_file_pattern": "active_####", "blender_version": self._blender_version_for_test,
            "render_settings": {RenderSettings.SAMPLES: 1}
        }
        paused_job_payload = {
            "name": "Paused Project Job", "asset_id": paused_asset_id,
            "output_file_pattern": "paused_####", "blender_version": self._blender_version_for_test,
            "render_settings": {RenderSettings.SAMPLES: 1}
        }

        active_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=active_job_payload)
        active_job_res.raise_for_status()
        paused_job_res = requests.post(f"{MANAGER_URL}/jobs/", json=paused_job_payload)
        paused_job_res.raise_for_status()

        active_job_id = active_job_res.json()['id']
        paused_job_id = paused_job_res.json()['id']

        requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/pause/").raise_for_status()

        active_job_done = False
        for _ in range(30):
            active_job_status = requests.get(f"{MANAGER_URL}/jobs/{active_job_id}/").json()['status']
            paused_job_status = requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status']
            assert paused_job_status == "QUEUED", "Paused job was processed prematurely!"
            if active_job_status == "DONE":
                active_job_done = True
                break
            time.sleep(2)
        assert active_job_done, "Active project job did not complete in time."

        requests.post(f"{MANAGER_URL}/projects/{paused_project_id}/unpause/").raise_for_status()

        paused_job_done = False
        for _ in range(30):
            if requests.get(f"{MANAGER_URL}/jobs/{paused_job_id}/").json()['status'] == "DONE":
                paused_job_done = True
                break
            time.sleep(2)
        assert paused_job_done, "Paused project job did not complete after being unpaused."


class TestForcedHardwareModes(BaseE2ETest):
    def test_force_cpu_only_mode(self):
        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_CPU_ONLY": "true"})

        response = requests.get(f"{MANAGER_URL}/heartbeat/")
        response.raise_for_status()
        worker_record = next((w for w in response.json() if w['hostname'] == socket.gethostname()), None)
        assert worker_record is not None
        assert worker_record.get('available_tools', {}).get('gpu_devices', []) == []

    def test_force_gpu_only_mode(self):
        is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        if not is_gpu_available() or (platform.system() == "Darwin" and is_ci):
            pytest.skip("Skipping GPU test: No GPU or in unstable macOS CI.")

        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue, extra_env={"SETHLANS_FORCE_GPU_ONLY": "true"})

        # FIX: Added the required 'output_file_pattern' key
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "GPU Job", "asset_id": self.scene_asset_id, "render_device": "GPU",
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "force_gpu_####"
        })
        gpu_res.raise_for_status()
        gpu_job = gpu_res.json()

        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "CPU Job", "asset_id": self.scene_asset_id, "render_device": "CPU",
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "force_cpu_####"
        })
        cpu_res.raise_for_status()
        cpu_job = cpu_res.json()

        gpu_job_completed = False
        for _ in range(30):
            assert requests.get(f"{MANAGER_URL}/jobs/{cpu_job['id']}/").json()['status'] == "QUEUED"
            if requests.get(f"{MANAGER_URL}/jobs/{gpu_job['id']}/").json()['status'] == "DONE":
                gpu_job_completed = True
                break
            time.sleep(2)
        assert gpu_job_completed


class TestDefaultWorkerFlexibility(BaseE2ETest):
    def test_gpu_capable_worker_handles_cpu_and_gpu_jobs(self):
        if not is_gpu_available() or (platform.system() == "Darwin" and os.environ.get("CI") == "true"):
            pytest.skip("Requires a stable GPU-capable host.")

        if self.worker_process and self.worker_process.poll() is None: self.worker_process.kill()
        self.start_worker(self.worker_log_queue)

        # FIX: Added the required 'output_file_pattern' key
        gpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "Flex GPU Job", "asset_id": self.scene_asset_id, "render_device": RenderDevice.GPU,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "flex_gpu_####"
        })
        gpu_res.raise_for_status()
        gpu_job = gpu_res.json()

        # FIX: Added the required 'output_file_pattern' key
        cpu_res = requests.post(f"{MANAGER_URL}/jobs/", json={
            "name": "Flex CPU Job", "asset_id": self.scene_asset_id, "render_device": RenderDevice.CPU,
            "blender_version": self._blender_version_for_test,
            "output_file_pattern": "flex_cpu_####"
        })
        cpu_res.raise_for_status()
        cpu_job = cpu_res.json()

        cpu_done, gpu_done = False, False
        for _ in range(120):
            if requests.get(f"{MANAGER_URL}/jobs/{gpu_job['id']}/").json()['status'] == "DONE": gpu_done = True
            if requests.get(f"{MANAGER_URL}/jobs/{cpu_job['id']}/").json()['status'] == "DONE": cpu_done = True
            if gpu_done and cpu_done: break
            time.sleep(2)

        assert gpu_done, "GPU job was not completed."
        assert cpu_done, "CPU job was not completed."