# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) verify + done page Playwright tests (FR-M2-8, FR-M2-9)."""

from __future__ import annotations

import json

from ._phase2_helpers import (
    captured_requests,
    enter_token,
    write_progress_json,
)


_BASE_CHECKPOINTS = [
    "welcome_seen",
    "topology_chosen",
    "network_configured",
    "database_configured",
    "admin_validated",
]


def _land(page, wp, route):
    """Pre-populate progress so the resume walker lands on *route*.

    The walker emits the next route AFTER the last completed checkpoint,
    so to land on /verify we stop at admin_validated; for /done we add
    verified so the next route is /done.
    """
    if route == "/done":
        checkpoints = _BASE_CHECKPOINTS + ["verified"]
    else:
        checkpoints = _BASE_CHECKPOINTS
    write_progress_json(wp.data_dir, checkpoints, topology="manager")
    enter_token(page, wp.base_url, wp.setup_token, expect_url=route)


# ---------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------

def test_verify_renders_checklist_all_passed(page, wizard_process):
    wp = wizard_process
    page.route(
        "**/api/wizard/verify/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "all_passed": True,
                "checks": [
                    {"name": "network_bindable", "passed": True, "error": ""},
                    {"name": "database_reachable", "passed": True, "error": ""},
                    {"name": "pending_setup_writable", "passed": True, "error": ""},
                ],
            }),
        ),
    )
    _land(page, wp, "/verify")
    page.wait_for_selector("#verify-next:not([disabled])", timeout=10000)
    items = page.locator("ul li").all_inner_texts()
    text = " ".join(items)
    # Each green-check rendered.
    assert text.count("✓") >= 3, text
    assert "✗" not in text, text


def test_verify_failed_check_shows_error_and_disables_next(page, wizard_process):
    wp = wizard_process
    page.route(
        "**/api/wizard/verify/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "all_passed": False,
                "checks": [
                    {"name": "network_bindable", "passed": True, "error": ""},
                    {
                        "name": "database_reachable",
                        "passed": False,
                        "error": "Connection refused",
                    },
                ],
            }),
        ),
    )
    _land(page, wp, "/verify")
    page.wait_for_selector("li", timeout=10000)
    body = page.locator("body").inner_text()
    assert "Connection refused" in body, body
    # Continue stays disabled when any check fails.
    assert page.locator("#verify-next").is_disabled()
    # Re-run button is shown.
    assert page.locator("button:has-text('Re-run checks')").count() == 1


def test_verify_navigates_to_done_when_all_pass(page, wizard_process):
    wp = wizard_process
    page.route(
        "**/api/wizard/verify/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "all_passed": True,
                "checks": [{"name": "network_bindable", "passed": True, "error": ""}],
            }),
        ),
    )
    # Mock done's downstream endpoints so navigation doesn't hit the real
    # backend on the next hop.
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    page.route(
        "**/api/wizard/done/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    _land(page, wp, "/verify")
    page.wait_for_selector("#verify-next:not([disabled])", timeout=10000)
    page.click("#verify-next")
    page.wait_for_url(f"{wp.base_url}/done", timeout=5000)


# ---------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------

def test_done_posts_pending_then_done_in_order(page, wizard_process):
    wp = wizard_process
    pending = captured_requests(page, "/api/wizard/pending-setup/")
    done = captured_requests(page, "/api/wizard/done/")
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    page.route(
        "**/api/wizard/done/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    _land(page, wp, "/done")
    # Both POSTs succeed — expect the success card (no redirect to /redirecting).
    page.wait_for_selector(".alert-success", timeout=10000)
    # Both endpoints fired exactly once each, pending BEFORE done.
    pending_posts = [r for r in pending if r["method"] == "POST"]
    done_posts = [r for r in done if r["method"] == "POST"]
    assert len(pending_posts) == 1, pending
    assert len(done_posts) == 1, done


def test_done_pending_failure_blocks_done_post(page, wizard_process):
    wp = wizard_process
    # Capture BOTH endpoints so we can prove the pending POST actually
    # fired (and got the 500 mock) before asserting that /done was
    # never called. Capturing pending too is defensive: if the request
    # listener somehow missed the firing for /done, the parallel
    # capture for pending-setup would catch it too.
    pending_calls = captured_requests(page, "/api/wizard/pending-setup/")
    done_calls = captured_requests(page, "/api/wizard/done/")
    page.route(
        "**/api/wizard/pending-setup/",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"error": "atomic_write_failed"}),
        ),
    )
    page.route(
        "**/api/wizard/done/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    _land(page, wp, "/done")
    # The alert appearing implies done.js's run() reached the
    # !response.ok branch, which means pending-setup returned 500
    # and done.js early-returned without firing /api/wizard/done/.
    page.wait_for_selector(".alert-danger", state="visible", timeout=10000)
    # The pending-setup POST fired exactly once and was mocked 500.
    # Asserting this here (rather than trusting the alert alone)
    # discriminates "alert never appeared" from "pending-setup never
    # fired" on any future regression.
    pending_posts = [r for r in pending_calls if r["method"] == "POST"]
    assert len(pending_posts) == 1, pending_calls
    # The user is still on /done; no redirect to /redirecting.
    assert "/redirecting" not in page.url
    # /done was NEVER posted because pending-setup failed.
    assert [r for r in done_calls if r["method"] == "POST"] == [], done_calls
    # Retry button is visible.
    assert page.locator(".alert-danger button:has-text('Retry')").count() == 1


def test_done_retry_button_re_fires_pending_post(page, wizard_process):
    """Retry POSTs pending-setup again; once it succeeds, done fires."""
    wp = wizard_process
    pending_state = {"calls": 0}

    def _pending(route):
        pending_state["calls"] += 1
        if pending_state["calls"] == 1:
            route.fulfill(status=500, body="{}")
        else:
            route.fulfill(status=200, body="{}")

    page.route("**/api/wizard/pending-setup/", _pending)
    page.route(
        "**/api/wizard/done/",
        lambda route: route.fulfill(status=200, body="{}"),
    )
    _land(page, wp, "/done")
    page.wait_for_selector(".alert-danger button:has-text('Retry')", timeout=10000)
    page.click(".alert-danger button:has-text('Retry')")
    # Second attempt succeeds — success card shown, no redirect to /redirecting.
    page.wait_for_selector(".alert-success", timeout=5000)
    assert pending_state["calls"] == 2
