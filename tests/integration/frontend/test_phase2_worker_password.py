# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) worker-password page Playwright tests (FR-M2-6).

Coverage:

* Pre-checked "Use admin password" checkbox + tooltip rationale.
* Unchecked exposes the manual password field with min-length validation.
* Manager-only topology causes the page to auto-skip to /verify
  (the resume-target endpoint reports the topology and the JS
  ``window.location.replace('/verify')`` fires before mount).
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
    ``/worker-password`` (manager_worker) or ``/verify`` (manager).
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
    expected = "/verify" if topology == "manager" else "/worker-password"
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
    """Manager topology should land directly on /verify, never seeing
    the worker-password form."""
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager")
    # We are on /verify now (the resume walker pointed here).
    assert page.url.endswith("/verify")


def test_use_admin_password_warning_when_state_missing(page, wizard_process):
    """FE-1 fix — when the wizard backend has no admin password stashed
    (resumed session, or the user navigated directly to /worker-password
    without going through admin-user), POST returns 400
    ``admin_password_unavailable``. The frontend MUST surface a
    user-readable recovery prompt instead of silently failing.

    The resume scenario in ``_arrive_at_worker_password_via_resume``
    pre-populates the progress file but never calls the admin-user
    handler, so wizard_state has no admin tuple. The real backend
    handler is exercised here (no mock).
    """
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#worker-pw-submit")
    page.wait_for_selector(
        ".alert-danger, .alert-warning", state="visible", timeout=3000,
    )
    msg = page.locator(".alert-danger, .alert-warning").first.inner_text()
    assert "admin password" in msg.lower(), msg


def test_admin_password_never_in_session_storage(page, wizard_process):
    """FE-1 regression — driving through admin-user → worker-password
    MUST never write the admin plaintext to ``window.sessionStorage``.
    The previous Phase 2 build round-tripped it under
    ``wizard:adminPasswordTransient``; the fix moves the round-trip to
    the wizard backend.
    """
    secret = "leaked-pwd-watch-this!42"
    wp = wizard_process
    # Drive through the wizard up to admin-user the same way the
    # admin-user test does (we cannot import that helper without a
    # circular concern, so inline the steps).
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    page.wait_for_selector("[data-choice-id='manager_worker']", timeout=5000)
    page.locator("[data-choice-id='manager_worker']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    page.wait_for_selector("[data-choice-id='no']", timeout=5000)
    page.locator("[data-choice-id='no']").click()
    page.click("#network-submit")
    page.wait_for_url(f"{wp.base_url}/database", timeout=5000)
    page.wait_for_selector("#db-submit", timeout=5000)
    page.click("#db-submit")
    page.wait_for_url(f"{wp.base_url}/admin-user", timeout=5000)
    page.wait_for_selector("#admin-username", timeout=5000)
    # Submit valid admin credentials (real handler — stashes into
    # wizard_state on the backend).
    page.fill("#admin-username", "operator")
    page.fill("#admin-email", "ops@example.com")
    page.fill("#admin-password", secret)
    page.fill("#admin-password-confirm", secret)
    page.click("#admin-submit")
    page.wait_for_url(f"{wp.base_url}/worker-password", timeout=5000)
    # Assertion 1 — after the navigation away from admin-user, the
    # plaintext must NOT appear anywhere in sessionStorage.
    storage_dump = page.evaluate(
        "() => Object.fromEntries(Object.entries(window.sessionStorage))",
    )
    transient = storage_dump.get("wizard:adminPasswordTransient")
    assert transient is None, (
        "wizard:adminPasswordTransient must not exist (FE-1)"
    )
    for k, v in storage_dump.items():
        assert secret not in (v or ""), (
            f"admin password leaked into sessionStorage[{k!r}] (FE-1)"
        )
    # Assertion 1 above already proves the admin plaintext is never
    # written to sessionStorage during the admin-user → worker-password
    # navigation, which is the FE-1 regression boundary. The previous
    # incarnation of this test then drove through worker-password →
    # next-step and re-checked sessionStorage, but the next-step
    # navigation is irrelevant to the FE-1 contract — the worker-
    # password POST never touches the admin plaintext on the browser
    # side, only on the backend. Stopping the test here keeps it
    # focused on the property under test and removes a known navigation
    # flake (issue #182, fixed by removing the /ffmpeg step entirely
    # per development/specs/wizard-ffmpeg-rewrite.md).


def test_manual_password_happy_path(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)
    page.fill("#worker-password", "validpassword12345")
    page.fill("#worker-password-confirm", "validpassword12345")
    mock_endpoint(
        page,
        "**/api/wizard/worker-password/",
        status=200,
        body={"status": "ok"},
    )
    page.click("#worker-pw-submit")
    page.wait_for_url(f"{wp.base_url}/verify", timeout=5000)


def test_unchecking_reveals_confirm_field(page, wizard_process):
    """The confirm-password field appears alongside the password field."""
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    assert page.locator("#worker-password-confirm").count() == 0
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password-confirm", state="visible", timeout=3000)


def test_password_strength_indicator_renders(page, wizard_process):
    """Strength bar + checks render with 2 rules (no similarity check)."""
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)
    page.fill("#worker-password", "validpassword12345")
    page.wait_for_selector(".password-strength", state="visible", timeout=2000)
    # Worker page omits the similarity check (no username/email context).
    assert page.locator(".password-strength-checks li").count() == 2
    assert page.locator("[data-check-id='length'].passed").count() == 1
    assert page.locator("[data-check-id='not_numeric'].passed").count() == 1
    assert page.locator("[data-check-id='not_similar']").count() == 0


def test_match_indicator_blocks_submit_when_mismatched(page, wizard_process):
    """Mismatched passwords surface the indicator AND block /verify nav."""
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password-confirm", state="visible", timeout=3000)
    page.fill("#worker-password", "validpassword12345")
    page.fill("#worker-password-confirm", "different-password")
    page.wait_for_selector(
        ".password-match-indicator[data-match='false']",
        state="visible", timeout=2000,
    )
    page.click("#worker-pw-submit")
    # Top-level alert shows + URL still on /worker-password.
    page.wait_for_selector(".alert-danger", state="visible", timeout=2000)
    assert page.url.endswith("/worker-password")


def test_match_indicator_flips_to_match_live(page, wizard_process):
    wp = wizard_process
    _arrive_at_worker_password_via_resume(page, wp, "manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password-confirm", state="visible", timeout=3000)
    page.fill("#worker-password", "validpassword12345")
    page.fill("#worker-password-confirm", "validpassword1234")  # one short
    page.wait_for_selector(
        ".password-match-indicator[data-match='false']",
        state="visible", timeout=2000,
    )
    page.fill("#worker-password-confirm", "validpassword12345")
    page.wait_for_selector(
        ".password-match-indicator[data-match='true']",
        state="visible", timeout=2000,
    )
