# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E robustness tests.

Tests queue draining (multiple jobs processed serially) and paused
project exclusion (jobs from paused projects are not claimed).
"""

import logging
import shutil
import tempfile
import time
from pathlib import Path

import requests

from tests.e2e.helpers import (
    admin_login,
    poll_for_completion,
)
from tests.e2e.process_manager import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    REPO_ROOT,
    build_manager_env,
    build_worker_env,
    find_free_port,
    generate_secrets,
    kill_process_tree,
    setup_database,
    start_manager,
    start_worker,
    wait_for_manager,
    wait_for_worker,
)

logger = logging.getLogger(__name__)

TEST_SCENE = REPO_ROOT / "tests" / "assets" / "test_scene.blend"

FAST_RENDER_SETTINGS = {
    "cycles.samples": 4,
    "render.resolution_x": 128,
    "render.resolution_y": 128,
    "render.resolution_percentage": 100,
}


class TestRobustness:
    """Robustness tests for queue draining and paused projects."""

    @classmethod
    def setup_class(cls):
        """Start a fresh manager and worker pair."""
        cls.manager_proc = None
        cls.worker_proc = None
        cls.manager_stdout = ""
        cls.manager_stderr = ""
        cls.worker_stdout = ""
        cls.worker_stderr = ""

        cls.port = find_free_port()
        cls.base_url = f"https://127.0.0.1:{cls.port}"
        cls.test_secrets = generate_secrets()

        cls.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_robust_"))
        cls.db_path = cls.temp_dir / "test_e2e_db.sqlite3"
        cls.media_root = cls.temp_dir / "media"
        cls.media_root.mkdir(parents=True, exist_ok=True)

        cls.manager_env = build_manager_env(
            cls.db_path, cls.media_root,
            cls.test_secrets["enrollment_key"],
            cls.test_secrets["secret_key"],
            cls.port,
        )
        cls.worker_data_dir = cls.temp_dir / "worker_data"
        cls.worker_env = build_worker_env(
            cls.test_secrets["enrollment_key"],
            "127.0.0.1", cls.port,
            worker_data_dir=cls.worker_data_dir,
        )

        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url, proc=cls.manager_proc)

        cls.session = requests.Session()
        cls.session.verify = False
        admin_login(
            cls.session, cls.base_url, ADMIN_USERNAME, ADMIN_PASSWORD,
        )

        cls.worker_proc = start_worker(cls.worker_env)
        wait_for_worker(cls.session, cls.base_url, timeout=180)

        # Create project and upload asset.
        resp = cls.session.post(
            f"{cls.base_url}/api/projects/",
            json={"name": "Robustness Project"},
            timeout=30,
        )
        assert resp.status_code == 201
        cls.project_id = resp.json()["id"]

        with open(TEST_SCENE, "rb") as f:
            resp = cls.session.post(
                f"{cls.base_url}/api/assets/",
                data={
                    "name": "test_scene.blend",
                    "project": cls.project_id,
                },
                files={
                    "blend_file": (
                        "test_scene.blend", f,
                        "application/octet-stream",
                    ),
                },
                timeout=60,
            )
        assert resp.status_code == 201
        cls.asset_id = resp.json()["id"]

    @classmethod
    def teardown_class(cls):
        """Stop all processes and clean up."""
        if cls.worker_proc:
            cls.worker_stdout, cls.worker_stderr = kill_process_tree(
                cls.worker_proc,
            )
        if cls.manager_proc:
            cls.manager_stdout, cls.manager_stderr = kill_process_tree(
                cls.manager_proc,
            )
        try:
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _submit_job(self, name):
        """Helper to submit a simple CPU render job."""
        resp = self.session.post(
            f"{self.base_url}/api/jobs/",
            json={
                "name": name,
                "asset_id": self.asset_id,
                "output_file_pattern": f"renders/{name}_####.png",
                "start_frame": 1,
                "end_frame": 1,
                "render_engine": "CYCLES",
                "render_device": "CPU",
                "render_settings": FAST_RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Job submission failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["id"]

    def test_queue_draining(self):
        """Submit multiple jobs and verify all complete serially."""
        job_ids = []
        for i in range(3):
            job_id = self._submit_job(f"drain_{i}")
            job_ids.append(job_id)

        # Poll each job to completion.
        for job_id in job_ids:
            job_url = f"{self.base_url}/api/jobs/{job_id}/"
            result = poll_for_completion(
                self.session, job_url, timeout=480,
            )
            assert result["status"] == "DONE", (
                f"Job {job_id} did not complete. "
                f"Status: {result['status']}, "
                f"Error: {result.get('error_message', 'N/A')}"
            )

    def test_paused_project_exclusion(self):
        """Jobs from a paused project should not be claimed."""
        # Create a second project for pausing.
        resp = self.session.post(
            f"{self.base_url}/api/projects/",
            json={"name": "Paused Project"},
            timeout=30,
        )
        assert resp.status_code == 201
        paused_project_id = resp.json()["id"]

        # Upload asset to the paused project.
        with open(TEST_SCENE, "rb") as f:
            resp = self.session.post(
                f"{self.base_url}/api/assets/",
                data={
                    "name": "paused_scene.blend",
                    "project": paused_project_id,
                },
                files={
                    "blend_file": (
                        "paused_scene.blend", f,
                        "application/octet-stream",
                    ),
                },
                timeout=60,
            )
        assert resp.status_code == 201
        paused_asset_id = resp.json()["id"]

        # Pause the project BEFORE submitting jobs.
        resp = self.session.post(
            f"{self.base_url}/api/projects/{paused_project_id}/pause/",
            timeout=30,
        )
        assert resp.status_code == 200

        # Submit a job to the paused project.
        resp = self.session.post(
            f"{self.base_url}/api/jobs/",
            json={
                "name": "Paused Job",
                "asset_id": paused_asset_id,
                "output_file_pattern": "renders/paused_####.png",
                "start_frame": 1,
                "end_frame": 1,
                "render_engine": "CYCLES",
                "render_device": "CPU",
                "render_settings": FAST_RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201
        paused_job_id = resp.json()["id"]

        # Wait and verify the job stays QUEUED (not claimed).
        time.sleep(15)
        resp = self.session.get(
            f"{self.base_url}/api/jobs/{paused_job_id}/", timeout=30,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUEUED", (
            f"Paused project job should remain QUEUED, "
            f"got: {resp.json()['status']}"
        )

        # Unpause and verify the job gets picked up.
        resp = self.session.post(
            f"{self.base_url}/api/projects/{paused_project_id}/unpause/",
            timeout=30,
        )
        assert resp.status_code == 200

        job_url = f"{self.base_url}/api/jobs/{paused_job_id}/"
        result = poll_for_completion(
            self.session, job_url, timeout=240,
        )
        assert result["status"] == "DONE", (
            f"Job should complete after unpausing. "
            f"Status: {result['status']}"
        )
