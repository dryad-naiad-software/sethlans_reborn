# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E tests for core rendering workflows.

Tests the full pipeline: project creation, asset upload, job submission,
worker claiming, rendering, output upload, and image verification.
Covers single-frame CPU render, animation render, and tiled render.
"""

import logging
import shutil
import tempfile
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_login,
    poll_for_completion,
    verify_image_output,
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

# Paths to test .blend files.
TEST_SCENE = REPO_ROOT / "tests" / "assets" / "test_scene.blend"
ANIMATION_BLEND = REPO_ROOT / "tests" / "assets" / "animation.blend"

# Render settings for fast E2E tests.
FAST_RENDER_SETTINGS = {
    "cycles.samples": 4,
    "render.resolution_x": 128,
    "render.resolution_y": 128,
    "render.resolution_percentage": 100,
}


class TestCoreWorkflows:
    """Tests for the core rendering pipeline."""

    @classmethod
    def setup_class(cls):
        """Start manager and worker, create project and upload assets."""
        cls.manager_proc = None
        cls.worker_proc = None
        cls.manager_stdout = ""
        cls.manager_stderr = ""
        cls.worker_stdout = ""
        cls.worker_stderr = ""

        cls.port = find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.test_secrets = generate_secrets()

        cls.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_core_"))
        cls.db_path = cls.temp_dir / "test_e2e_db.sqlite3"
        cls.media_root = cls.temp_dir / "media"
        cls.media_root.mkdir(parents=True, exist_ok=True)

        cls.manager_env = build_manager_env(
            cls.db_path, cls.media_root,
            cls.test_secrets["enrollment_key"],
            cls.test_secrets["secret_key"],
            cls.port,
        )
        cls.worker_env = build_worker_env(
            cls.test_secrets["enrollment_key"],
            "127.0.0.1", cls.port,
        )

        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url)

        cls.session = requests.Session()
        admin_login(
            cls.session, cls.base_url, ADMIN_USERNAME, ADMIN_PASSWORD,
        )

        cls.worker_proc = start_worker(cls.worker_env)
        cls.worker_data = wait_for_worker(
            cls.session, cls.base_url, timeout=180,
        )

        # Create project (uses default SupportedBlenderVersion from migration).
        resp = cls.session.post(
            f"{cls.base_url}/api/projects/",
            json={"name": "E2E Test Project"},
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Project creation failed: {resp.status_code} {resp.text}"
        )
        cls.project_id = resp.json()["id"]

        # Upload test scene asset.
        cls.scene_asset_id = cls._upload_asset(
            "test_scene.blend", TEST_SCENE,
        )

        # Upload animation asset if it exists.
        cls.animation_asset_id = None
        if ANIMATION_BLEND.exists():
            cls.animation_asset_id = cls._upload_asset(
                "animation.blend", ANIMATION_BLEND,
            )

    @classmethod
    def _upload_asset(cls, name, file_path):
        """Upload a .blend file and return its asset ID."""
        with open(file_path, "rb") as f:
            resp = cls.session.post(
                f"{cls.base_url}/api/assets/",
                data={"name": name, "project": cls.project_id},
                files={"blend_file": (name, f, "application/octet-stream")},
                timeout=60,
            )
        assert resp.status_code == 201, (
            f"Asset upload failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["id"]

    @classmethod
    def teardown_class(cls):
        """Stop worker and manager, clean up temp files."""
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

    def test_single_frame_cpu_render(self):
        """Submit a single-frame CPU job and verify rendered output."""
        resp = self.session.post(
            f"{self.base_url}/api/jobs/",
            json={
                "name": "E2E Single Frame CPU",
                "asset_id": self.scene_asset_id,
                "output_file_pattern": "renders/single_####",
                "start_frame": 1,
                "end_frame": 1,
                "render_engine": "CYCLES",
                "render_device": "CPU",
                "render_settings": FAST_RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Job creation failed: {resp.status_code} {resp.text}"
        )
        job_id = resp.json()["id"]
        job_url = f"{self.base_url}/api/jobs/{job_id}/"

        result = poll_for_completion(self.session, job_url, timeout=300)
        assert result["status"] == "DONE", (
            f"Job did not complete successfully. Status: {result['status']}, "
            f"Error: {result.get('error_message', 'N/A')}"
        )

        output_file = result.get("output_file")
        assert output_file, "Job completed but has no output_file"
        verify_image_output(self.session, output_file, self.base_url)

    @pytest.mark.skipif(
        not (REPO_ROOT / "tests" / "assets" / "animation.blend").exists(),
        reason="animation.blend not found in tests/assets/",
    )
    def test_animation_render(self):
        """Submit a multi-frame animation and verify completion."""
        assert self.animation_asset_id, "Animation asset not uploaded"

        resp = self.session.post(
            f"{self.base_url}/api/animations/",
            json={
                "name": "E2E Animation",
                "project": self.project_id,
                "asset_id": self.animation_asset_id,
                "output_file_pattern": "renders/anim_####",
                "start_frame": 1,
                "end_frame": 3,
                "frame_step": 1,
                "render_engine": "CYCLES",
                "render_device": "CPU",
                "render_settings": FAST_RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Animation creation failed: {resp.status_code} {resp.text}"
        )
        anim_id = resp.json()["id"]
        anim_url = f"{self.base_url}/api/animations/{anim_id}/"

        result = poll_for_completion(
            self.session, anim_url, timeout=480,
        )
        assert result["status"] == "DONE", (
            f"Animation did not complete. Status: {result['status']}"
        )
        assert result["total_frames"] == 3
        assert result["completed_frames"] == 3

    def test_tiled_render(self):
        """Submit a 2x2 tiled job and verify assembly."""
        resp = self.session.post(
            f"{self.base_url}/api/tiled-jobs/",
            json={
                "name": "E2E Tiled Render",
                "project": self.project_id,
                "asset_id": self.scene_asset_id,
                "final_resolution_x": 128,
                "final_resolution_y": 128,
                "tile_count_x": 2,
                "tile_count_y": 2,
                "render_engine": "CYCLES",
                "render_device": "CPU",
                "render_settings": FAST_RENDER_SETTINGS,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Tiled job creation failed: {resp.status_code} {resp.text}"
        )
        tiled_id = resp.json()["id"]
        tiled_url = f"{self.base_url}/api/tiled-jobs/{tiled_id}/"

        result = poll_for_completion(
            self.session, tiled_url, timeout=480,
        )
        assert result["status"] == "DONE", (
            f"Tiled job did not complete. Status: {result['status']}"
        )
        assert result["total_tiles"] == 4
        assert result["completed_tiles"] == 4

        output_file = result.get("output_file")
        if output_file:
            img = verify_image_output(
                self.session, output_file, self.base_url,
            )
            assert img.width == 128 and img.height == 128, (
                f"Assembled image has wrong dimensions: "
                f"{img.width}x{img.height}"
            )
