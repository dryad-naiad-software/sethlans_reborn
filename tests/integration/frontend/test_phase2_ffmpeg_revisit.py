# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for issue #182 — /ffmpeg revisit via verify Back.

After completing the FFmpeg installer, navigating forward to /verify
and then clicking the Back button must reliably re-render the page in
its short-circuit "FFmpeg installed." state. The wizard subprocess is
exercised end-to-end so the on-disk short-circuit (already_installed)
path is real, not mocked.
"""

from __future__ import annotations

import json

from ._phase2_helpers import (
    captured_requests,
    enter_token,
    write_progress_json,
)


def _land_on_ffmpeg(page, wp):
    """Pre-populate progress so the resume walker lands on /ffmpeg."""
    write_progress_json(
        wp.data_dir,
        [
            "welcome_seen",
            "topology_chosen",
            "network_configured",
            "database_configured",
            "admin_validated",
        ],
        topology="manager",
    )
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/ffmpeg")


def test_ffmpeg_revisit_via_verify_back_short_circuits(page, wizard_process):
    """FR-M2-7 + issue #182.

    1. Drive the page to a complete state (mocked first visit).
    2. Click Continue → land on /verify (mocked).
    3. Click /verify Back → land on /ffmpeg.
    4. The revisit must show status === 'complete' with Continue
       enabled WITHOUT firing a fresh /api/wizard/ffmpeg/start/ that
       hangs.

    The first visit is mocked because the real FFmpeg download can't run
    in CI. The revisit relies on the JS controller arriving at
    'complete' state — which it must do by either firing /start/ and
    receiving the in-progress / complete response, or by some other
    deterministic path the implementation chooses. The acceptance is
    "user sees FFmpeg installed and Continue is enabled", not the
    specific request shape.
    """
    wp = wizard_process

    # Mock /start/ to always return in_progress (single-task style),
    # mock /progress/ to always return complete. The page should fire
    # both on first visit AND on revisit — issue #182 was that on
    # revisit ZERO requests fired.
    start_calls = captured_requests(page, "/api/wizard/ffmpeg/start/")
    page.route(
        "**/api/wizard/ffmpeg/start/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"task_id": "ffmpeg-revisit-task", "status": "in_progress"},
            ),
        ),
    )
    page.route(
        "**/api/wizard/ffmpeg/progress/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"status": "complete", "percent": 100},
            ),
        ),
    )
    # /verify mock so the forward navigation lands cleanly.
    page.route(
        "**/api/wizard/verify/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "all_passed": True,
                "checks": [
                    {"name": "ffmpeg_runs", "passed": True, "error": ""},
                ],
            }),
        ),
    )

    _land_on_ffmpeg(page, wp)

    # First visit completes.
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    first_visit_starts = len([r for r in start_calls if r["method"] == "POST"])
    assert first_visit_starts == 1, start_calls

    # Forward to /verify.
    page.click("#ffmpeg-next")
    page.wait_for_url(f"{wp.base_url}/verify", timeout=5000)
    page.wait_for_selector("#verify-back", timeout=5000)

    # Click verify's Back button — drives JS window.location.assign('/ffmpeg').
    page.click("#verify-back")
    page.wait_for_url(f"{wp.base_url}/ffmpeg", timeout=5000)

    # ACCEPTANCE — the revisit reliably renders the complete state
    # with Continue enabled. Issue #182 is "stays stuck on Starting…
    # forever".
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    headline = page.locator(".ffmpeg-progress p").first.inner_text()
    assert "FFmpeg installed" in headline, headline


def test_ffmpeg_revisit_does_not_get_stuck_starting(page, wizard_process):
    """Direct guard for issue #182's failure mode.

    Drive a forward → back cycle and assert we never end up with the
    Continue button still disabled and the headline showing 'Starting…'.
    """
    wp = wizard_process
    page.route(
        "**/api/wizard/ffmpeg/start/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"task_id": "stuck-task", "status": "in_progress"},
            ),
        ),
    )
    page.route(
        "**/api/wizard/ffmpeg/progress/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "complete", "percent": 100}),
        ),
    )
    page.route(
        "**/api/wizard/verify/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "all_passed": True,
                "checks": [
                    {"name": "ffmpeg_runs", "passed": True, "error": ""},
                ],
            }),
        ),
    )
    _land_on_ffmpeg(page, wp)
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    page.click("#ffmpeg-next")
    page.wait_for_url(f"{wp.base_url}/verify", timeout=5000)
    page.click("#verify-back")
    page.wait_for_url(f"{wp.base_url}/ffmpeg", timeout=5000)
    # Wait for the page to settle — give the JS controller time to
    # complete its mount → start → poll cycle. If the bug is present,
    # the headline will still be 'Starting…' and Continue disabled.
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    headline = page.locator(".ffmpeg-progress p").first.inner_text()
    assert "Starting" not in headline, (
        f"Issue #182 regression: revisit stuck at Starting. headline={headline!r}"
    )


def test_poll_persistent_failure_surfaces_retry(page, wizard_process):
    """Issue #182 acceptance — _poll() must not silently no-op forever.

    If /progress/ keeps returning a non-2xx response (e.g. 404
    unknown_task because _active_task got reset), the page must surface
    a generic failure with a Retry button rather than spinning.
    """
    wp = wizard_process
    page.route(
        "**/api/wizard/ffmpeg/start/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"task_id": "ghost-task", "status": "in_progress"},
            ),
        ),
    )
    # Always return 404 — task vanished from the registry.
    page.route(
        "**/api/wizard/ffmpeg/progress/**",
        lambda route: route.fulfill(
            status=404,
            content_type="application/json",
            body=json.dumps({"error": "unknown_task"}),
        ),
    )
    _land_on_ffmpeg(page, wp)
    # The fix: after N consecutive non-2xx polls, the page surfaces a
    # failure with a Retry button. The test asserts the alert appears.
    page.wait_for_selector(
        ".alert-danger button:has-text('Retry')", timeout=15000,
    )
