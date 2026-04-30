# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) token-entry page Playwright tests.

Drives the live wizard subprocess + a real Chromium browser through
the auth flow on ``index.html`` (served at ``/token``):

* Happy path: paste valid token → land on the welcome page (``/``).
* Wrong token → 403 → inline error renders, no navigation.
* Resume after re-auth: pre-populate ``.setup_progress.json`` so the
  resume-target endpoint walks past welcome/topology/network/database
  and lands the user on ``/admin-user`` with a friendly banner
  consumed on arrival.
* Resume target endpoint failure falls back to ``/`` without crashing.

The wizard now binds plain HTTP on loopback (issue #170) so no TLS
override is required for the navigation itself; the
``browser_context_args`` override in ``conftest.py`` keeps the test
robust against future fronting.
"""

from __future__ import annotations

import pytest

from ._phase2_helpers import (
    enter_token,
    get_progress_json,
    write_progress_json,
)


def test_happy_path_lands_on_welcome(page, wizard_process):
    wp = wizard_process
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    # Welcome page renders its <h1>Welcome heading after Petite-vue mount.
    assert page.locator("h1").inner_text() == "Welcome"


def test_wrong_token_shows_inline_error(page, wizard_process):
    wp = wizard_process
    page.goto(f"{wp.base_url}/token")
    page.wait_for_selector("#setup-token")
    page.fill("#setup-token", "definitely-not-the-token-aaaaaaaa")
    page.wait_for_function(
        "() => !document.querySelector('button[type=\"submit\"]').disabled",
    )
    page.click("button[type='submit']")
    # Error shown inline (role=alert), URL unchanged (still /token).
    page.wait_for_selector("#setup-token-error", state="visible", timeout=3000)
    assert "Invalid setup token" in page.locator("#setup-token-error").inner_text()
    assert page.url.endswith("/token")


def test_resume_redirects_to_admin_user_with_banner(page, wizard_process):
    """FR-CHK3-RESUME — pre-populate progress with welcome+topology+network+
    database checkpoints; the resume walker should land on /admin-user
    and display the friendly resume banner.
    """
    wp = wizard_process
    write_progress_json(
        wp.data_dir,
        [
            "welcome_seen",
            "topology_chosen",
            "network_configured",
            "database_configured",
        ],
        topology="manager_worker",
    )
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/admin-user")
    # Banner is read-once on arrival; the destination page renders it
    # via consumeResumeBanner. Label MUST come from RESUME_STEP_LABELS
    # — "Admin User" is the human-readable mapping for admin_validated.
    banner = page.locator("[role='status']").first
    banner.wait_for(state="visible", timeout=3000)
    text = banner.inner_text()
    assert "session expired" in text.lower(), text
    assert "Admin User" in text, text


def test_resume_target_failure_falls_back_to_root(page, wizard_process):
    """If the resume-target endpoint errors, auth.js falls back to /."""
    wp = wizard_process
    # Intercept the resume-target call BEFORE clicking Continue.
    page.route(
        "**/api/wizard/resume-target/",
        lambda route: route.fulfill(status=500, body=""),
    )
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    assert page.locator("h1").inner_text() == "Welcome"


def test_token_entry_serves_at_token_path(page, wizard_process):
    """``/`` is the welcome page in Phase 2; legacy index.html lives at /token."""
    wp = wizard_process
    page.goto(f"{wp.base_url}/token")
    page.wait_for_selector("#setup-token")
    # Title and label parity with index.html — sanity check that the
    # right HTML is served at /token, not the old root mount.
    assert "Setup" in page.title()


def test_token_entry_noscript_block_present(page, wizard_process):
    """index.html must include the noscript fallback block."""
    wp = wizard_process
    page.goto(f"{wp.base_url}/token")
    # Even with JS on, the <noscript> element exists in the DOM tree.
    assert page.locator("noscript").count() > 0


@pytest.mark.parametrize("topology,expected_route", [
    ("manager", "/verify"),
    ("manager_worker", "/worker-password"),
])
def test_resume_walker_topology_aware(
    page, wizard_process, topology, expected_route,
):
    """Manager-only topology auto-skips worker_password_set so the
    resume walker lands on /verify; manager_worker still visits
    /worker-password.
    """
    wp = wizard_process
    write_progress_json(
        wp.data_dir,
        [
            "welcome_seen",
            "topology_chosen",
            "network_configured",
            "database_configured",
            "admin_validated",
        ],
        topology=topology,
    )
    enter_token(page, wp.base_url, wp.setup_token, expect_url=expected_route)


def test_progress_file_unchanged_by_resume_lookup(page, wizard_process):
    """Resume-target walker must NOT mutate the progress file."""
    wp = wizard_process
    write_progress_json(
        wp.data_dir,
        ["welcome_seen", "topology_chosen"],
        topology="manager",
    )
    before = get_progress_json(wp.data_dir)
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/network")
    after = get_progress_json(wp.data_dir)
    assert before == after, (before, after)
