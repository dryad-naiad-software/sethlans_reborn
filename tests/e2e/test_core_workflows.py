# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E tests for core rendering workflows.

Tests the full pipeline: project creation, asset upload, job submission,
worker claiming, rendering, output upload, and image verification.
Covers single-frame CPU render, animation render, and tiled render.

The class-scoped manager + worker harness (boot, asset upload, teardown,
log dumps) lives in ``tests/e2e/core_workflows_harness.py``.
"""

import pytest

from tests.e2e.core_workflows_harness import (
    ANIMATION_BLEND,
    FAST_RENDER_SETTINGS,
    CoreWorkflowsHarness,
)
from tests.e2e.helpers import (
    poll_for_completion,
    verify_image_output,
)


class TestCoreWorkflows(CoreWorkflowsHarness):
    """Tests for the core rendering pipeline."""

    def test_single_frame_cpu_render(self):
        """Submit a single-frame CPU job and verify rendered output."""
        resp = self.session.post(
            f"{self.base_url}/api/jobs/",
            json={
                "name": "E2E Single Frame CPU",
                "asset_id": self.scene_asset_id,
                "output_file_pattern": "renders/single_####.png",
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

        # First test in the class — absorbs cold-worker overhead
        # (blender.org scrape + download + extract). Subsequent tests
        # reuse the cached Blender and can keep tighter budgets.
        result = poll_for_completion(self.session, job_url, timeout=900)
        assert result["status"] == "DONE", (
            f"Job did not complete successfully. Status: {result['status']}, "
            f"Error: {result.get('error_message', 'N/A')}"
        )

        output_file = result.get("output_file")
        assert output_file, "Job completed but has no output_file"
        verify_image_output(self.session, output_file, self.base_url)

    @pytest.mark.skipif(
        not ANIMATION_BLEND.exists(),
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
                "output_file_pattern": "renders/anim_####.png",
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

        # 1200s covers both the render itself (3 frames) and the
        # cold-start allowance if this test runs first under pytest -k.
        result = poll_for_completion(
            self.session, anim_url, timeout=1200,
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

        # 1200s covers the 4-tile render and the cold-start allowance
        # if this test runs first under pytest -k.
        result = poll_for_completion(
            self.session, tiled_url, timeout=1200,
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
