# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Playwright integration tests for the done-page disambiguator (issue #205).

Three scenarios exercise FR-DONE1, FR-DONE2, FR-DONE4, FR-HTML1, FR-HTML2:

1. pending-setup 200 + done transport error  → success card visible (expected race)
2. pending-setup transport error             → error card visible (real failure)
3. pending-setup 200 + done 200              → success card visible (normal path)

The ``fetchChromeContext`` call on every done-page mount hits
``/api/wizard/resume-target/`` — its catch block swallows all failures and
returns ``{topology: null, checkpoints: []}``.  No stub is required.
"""

from __future__ import annotations

from ._phase2_helpers import enter_token, write_progress_json

_BASE_CHECKPOINTS = [
    "welcome_seen",
    "topology_chosen",
    "network_configured",
    "database_configured",
    "admin_validated",
    "verified",
]


def _land(page, wp, route):
    """Pre-populate progress so the resume walker lands on *route*."""
    write_progress_json(wp.data_dir, _BASE_CHECKPOINTS, topology="manager")
    enter_token(page, wp.base_url, wp.setup_token, expect_url=route)


# ---------------------------------------------------------------------
# Case 1 — pending-setup 200 + done transport error → success card
# ---------------------------------------------------------------------

def test_done_transport_error_after_pending_success_shows_success_card(
    page, wizard_process,
):
    """FR-DONE1 / FR-HTML1 / FR-HTML2: transport error on done POST after
    pending-setup 200 shows the success card, not the error card."""
    wp = wizard_process
    # Routes MUST be registered BEFORE _land navigates — the page mount
    # fires both fetches immediately, so a route registered after _land
    # would miss them.
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.fulfill(
            status=200, body="{}", content_type="application/json",
        ),
    )
    # Simulate TCP reset by aborting the done request.
    page.route(
        "**/api/wizard/done/",
        lambda route: route.abort("connectionaborted"),
    )
    _land(page, wp, "/done")

    # Wait for the JS to settle (both fetches complete / reject).
    page.wait_for_selector(".alert-success", timeout=5000)

    assert page.locator(".alert-success").is_visible()
    assert not page.locator(".alert-danger").is_visible()
    assert "Setup complete" in page.locator(".alert-success").inner_text()
    assert "dashboard will open in a new tab" in page.locator(".alert-success").inner_text()


# ---------------------------------------------------------------------
# Case 2 — pending-setup transport error → error card
# ---------------------------------------------------------------------

def test_pending_setup_failure_shows_error_card(page, wizard_process):
    """FR-DONE2: pending-setup transport error keeps the existing red error card."""
    wp = wizard_process
    # Register the route BEFORE _land — page mount fires the fetch immediately.
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.abort("connectionaborted"),
    )
    _land(page, wp, "/done")

    page.wait_for_selector(".alert-danger", timeout=5000)

    assert page.locator(".alert-danger").is_visible()
    assert not page.locator(".alert-success").is_visible()


# ---------------------------------------------------------------------
# Case 3 — pending-setup 200 + done 200 → success card (no /redirecting)
# ---------------------------------------------------------------------

def test_done_200_after_pending_success_shows_success_card(page, wizard_process):
    """FR-DONE4: done POST returning 200 also shows the success card
    (not /redirecting, because the launcher opens the dashboard tab itself)."""
    wp = wizard_process
    # Register routes BEFORE _land — page mount fires both fetches immediately.
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.fulfill(
            status=200, body="{}", content_type="application/json",
        ),
    )
    page.route(
        "**/api/wizard/done/",
        lambda route: route.fulfill(
            status=200, body="{}", content_type="application/json",
        ),
    )
    _land(page, wp, "/done")

    page.wait_for_selector(".alert-success", timeout=5000)

    assert page.locator(".alert-success").is_visible()
    assert not page.locator(".alert-danger").is_visible()
    # Must NOT redirect to /redirecting.
    assert "/redirecting" not in page.url
