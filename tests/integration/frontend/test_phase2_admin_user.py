# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) admin-user page Playwright tests (FR-M2-5)."""

from __future__ import annotations

import json

from ._phase2_helpers import enter_token, mock_endpoint


def _arrive_at_admin_user(page, wp):
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    page.wait_for_selector("[data-choice-id='manager']", timeout=5000)
    page.locator("[data-choice-id='manager']").click()
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


def test_empty_form_shows_validation_error(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.click("#admin-submit")
    page.wait_for_selector(".alert-danger", state="visible", timeout=3000)
    msg = page.locator(".alert-danger").inner_text()
    assert "every field" in msg.lower() or "fill" in msg.lower(), msg


def test_password_mismatch_shows_inline_error(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "admin")
    page.fill("#admin-email", "admin@example.com")
    page.fill("#admin-password", "MyP@ssw0rd1!")
    page.fill("#admin-password-confirm", "different-password")
    page.click("#admin-submit")
    page.wait_for_selector(".alert-danger", state="visible", timeout=3000)
    msg = page.locator(".alert-danger").inner_text()
    assert "do not match" in msg.lower(), msg


def test_backend_validator_failures_render(page, wizard_process):
    """Mocked backend rejects with `password_invalid` + failures array."""
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    mock_endpoint(
        page,
        "**/api/wizard/admin-user/",
        status=400,
        body={
            "error": "password_invalid",
            "failures": ["password_too_common", "password_too_short"],
        },
    )
    page.fill("#admin-username", "admin")
    page.fill("#admin-email", "admin@example.com")
    page.fill("#admin-password", "abc")
    page.fill("#admin-password-confirm", "abc")
    page.click("#admin-submit")
    page.wait_for_selector(".alert-danger ul li", timeout=3000)
    items = page.locator(".alert-danger ul li").all_inner_texts()
    text = " ".join(items).lower()
    assert "common" in text, items
    assert "8 characters" in text, items


def test_happy_path_navigates_to_worker_password(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    mock_endpoint(
        page,
        "**/api/wizard/admin-user/",
        status=200,
        body={"status": "ok"},
    )
    page.fill("#admin-username", "admin")
    page.fill("#admin-email", "admin@example.com")
    page.fill("#admin-password", "MyV3ry-Strong!Password")
    page.fill("#admin-password-confirm", "MyV3ry-Strong!Password")
    page.click("#admin-submit")
    page.wait_for_url(f"{wp.base_url}/worker-password", timeout=5000)


def test_passwords_not_stashed_in_form_state(page, wizard_process):
    """FR-CHK3-FORM-STATE — admin password fields NEVER stashed."""
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "operator")
    page.fill("#admin-email", "ops@example.com")
    page.fill("#admin-password", "leaked-password-must-not-stash")
    page.fill("#admin-password-confirm", "leaked-password-must-not-stash")
    # Trigger a stash (the @input handler on the username/email fires on
    # any keystroke). Submit a no-op so _stash runs explicitly too.
    mock_endpoint(
        page,
        "**/api/wizard/admin-user/",
        status=400,
        body={"error": "username_required"},
    )
    page.click("#admin-submit")
    raw = page.evaluate(
        "() => window.sessionStorage.getItem('wizard.form.admin-user')",
    )
    if raw is not None:
        parsed = json.loads(raw)
        # Username/email may be stashed; passwords MUST NOT be.
        assert "password" not in parsed, parsed
        assert "passwordConfirm" not in parsed, parsed
        # Inverse: also assert the stashed VALUES don't contain the secret.
        for v in parsed.values():
            if isinstance(v, str):
                assert "leaked-password-must-not-stash" not in v, parsed
