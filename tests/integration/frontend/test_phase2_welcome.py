# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) welcome page Playwright tests (FR-M2-1)."""

from __future__ import annotations

from ._phase2_helpers import enter_token, get_progress_json


def test_welcome_renders_branding(page, wizard_process):
    wp = wizard_process
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    assert page.locator("h1").inner_text() == "Welcome"
    body_text = page.locator("body").inner_text()
    assert "topology" in body_text.lower()


def test_next_button_records_checkpoint_and_navigates(page, wizard_process):
    wp = wizard_process
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    progress = get_progress_json(wp.data_dir)
    assert "welcome_seen" in progress.get("checkpoints", []), progress


def test_idempotent_next_does_not_double_record(page, wizard_process):
    wp = wizard_process
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    # Navigate back manually (simulating a back-button press) and click
    # Next again. The checkpoint append is idempotent at the handler.
    page.go_back()
    page.wait_for_selector("#welcome-next")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    progress = get_progress_json(wp.data_dir)
    seen_count = progress.get("checkpoints", []).count("welcome_seen")
    assert seen_count == 1, progress
