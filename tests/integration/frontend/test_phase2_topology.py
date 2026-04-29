# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) topology page Playwright tests (FR-M2-2 / FR-CHK4).

The Phase 2 contract change for this page: clicking Continue MUST NOT
fire ``POST /api/wizard/done/``. The done endpoint is only fired from
the verify/done step. This module pins that contract via Playwright's
network-request capture.
"""

from __future__ import annotations

import pytest

from ._phase2_helpers import (
    captured_requests,
    enter_token,
    get_progress_json,
)


def _arrive_at_topology(page, wp):
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)


def test_radiogroup_keyboard_navigation(page, wizard_process):
    wp = wizard_process
    _arrive_at_topology(page, wp)
    # The first card should be focusable; Arrow keys cycle.
    page.locator("[role='radio']").first.focus()
    page.keyboard.press("ArrowRight")
    # Card 2 (manager_worker) should be aria-checked=true now.
    cards = page.locator("[role='radio']")
    assert cards.nth(1).get_attribute("aria-checked") == "true"
    page.keyboard.press("End")
    assert cards.nth(2).get_attribute("aria-checked") == "true"
    page.keyboard.press("Home")
    assert cards.nth(0).get_attribute("aria-checked") == "true"


@pytest.mark.parametrize("topology,expected_path", [
    ("manager", "/network"),
    ("manager_worker", "/network"),
])
def test_continue_navigates_to_network(
    page, wizard_process, topology, expected_path,
):
    wp = wizard_process
    _arrive_at_topology(page, wp)
    page.locator(f"[data-choice-id='{topology}']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}{expected_path}", timeout=5000)


def test_worker_only_attempts_find_manager_route(page, wizard_process):
    wp = wizard_process
    _arrive_at_topology(page, wp)
    page.locator("[data-choice-id='worker_only']").click()
    # The /find-manager route is a Spec 3 placeholder — it 404s today.
    # We assert the navigation ATTEMPT lands on /find-manager (the URL
    # the browser tried to load, irrespective of the response status).
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/find-manager", timeout=5000)


def test_continue_does_not_post_done(page, wizard_process):
    """FR-M2-2 — Continue MUST NOT fire POST /api/wizard/done/."""
    wp = wizard_process
    _arrive_at_topology(page, wp)
    done_calls = captured_requests(page, "/api/wizard/done/")
    page.locator("[data-choice-id='manager']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    # Filter to only POST-method captures (a GET would be unexpected too,
    # but the contract is specifically about POST).
    posts = [r for r in done_calls if r["method"] == "POST"]
    assert posts == [], f"Unexpected /api/wizard/done/ POSTs: {posts}"


def test_topology_chosen_checkpoint_recorded(page, wizard_process):
    wp = wizard_process
    _arrive_at_topology(page, wp)
    page.locator("[data-choice-id='manager']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    progress = get_progress_json(wp.data_dir)
    assert "topology_chosen" in progress.get("checkpoints", []), progress
    # Topology field captures the chosen value.
    assert progress.get("topology") == "manager", progress
