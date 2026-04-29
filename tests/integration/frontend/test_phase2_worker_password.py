# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) worker-password page Playwright tests (FR-M2-6).

Coverage:

* Pre-checked "Use admin password" checkbox + tooltip rationale.
* Unchecked exposes the manual password field with min-length validation.
* Manager-only topology causes the page to auto-skip to /ffmpeg
  (the resume-target endpoint reports the topology and the JS
  ``window.location.replace('/ffmpeg')`` fires before mount).
"""

from __future__ import annotations

from ._phase2_helpers import (
    enter_token,
    mock_endpoint,
    write_progress_json,
)


def _arrive_at_worker_password_via_resume(page, wp, topology):
    """Skip the wizard's earlier pages by pre-populating progress + topology
    and using the resume-target route to land directly on
    ``/worker-password`` (manager_worker) or ``/ffmpeg`` (manager).
    """
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
    expected = "/ffmpeg" if topology == "manager" else "/worker-password"
    enter_token(page, wp.base_url, wp.setup_token, expect_url=expected)


def test_use_admin_checkbox_pre_checked(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    cb = page.locator("#use-admin-checkbox")
    cb.wait_for(state="visible")
    page.wait_for_function(
        "() => document.querySelector('#use-admin-checkbox').checked === true",
        timeout=3000,
    )
    assert cb.is_checked()


def test_unchecking_reveals_password_field(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    # Manual password input is hidden when checkbox is checked.
    assert page.locator("#worker-password").count() == 0
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)


def test_short_password_inline_validation(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)
    page.fill("#worker-password", "short")
    page.click("#worker-pw-submit")
    page.wait_for_selector(".invalid-feedback", state="visible", timeout=3000)
    msg = page.locator(".invalid-feedback").inner_text()
    assert "8 characters" in msg, msg


def test_manager_only_auto_skips(page, wizard_process):
    """Manager topology should land directly on /ffmpeg, never seeing
    the worker-password form."""
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager")
    # We are on /ffmpeg now (the resume walker pointed here).
    assert page.url.endswith("/ffmpeg")


def test_use_admin_password_warning_when_pw_unavailable(page, wizard_process):
    """Manager_worker resume — admin pw is NOT in sessionStorage. The
    "use admin password" checkbox is on by default; submit produces a
    user-readable warning rather than silently failing.
    """
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#worker-pw-submit")
    page.wait_for_selector(".alert-danger, .alert-warning", state="visible", timeout=3000)
    msg = page.locator(".alert-danger, .alert-warning").first.inner_text()
    assert "admin password" in msg.lower(), msg


def test_manual_password_happy_path(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)
    page.fill("#worker-password", "validpassword12345")
    mock_endpoint(
        page,
        "**/api/wizard/worker-password/",
        status=200,
        body={"status": "ok"},
    )
    page.click("#worker-pw-submit")
    page.wait_for_url(f"{wp.base_url}/ffmpeg", timeout=5000)
