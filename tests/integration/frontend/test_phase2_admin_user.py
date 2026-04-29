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
    """FR-CHK3-FORM-STATE — admin password fields NEVER stashed.

    Updated for FE-1 (the admin password sessionStorage leak fix): in
    addition to the form-state stash key, this test scans the entire
    sessionStorage for the typed plaintext to make sure no other code
    path (including the previous ``wizard:adminPasswordTransient``
    round-trip) writes it to storage.
    """
    secret = "leaked-password-must-not-stash"
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "operator")
    page.fill("#admin-email", "ops@example.com")
    page.fill("#admin-password", secret)
    page.fill("#admin-password-confirm", secret)
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
                assert secret not in v, parsed
    # FE-1 regression — the previous Phase 2 build wrote the admin
    # password under ``wizard:adminPasswordTransient``. That key MUST
    # never appear in storage, AND no other key may contain the typed
    # secret value either (defense against future ad-hoc stashes).
    transient = page.evaluate(
        "() => window.sessionStorage.getItem('wizard:adminPasswordTransient')",
    )
    assert transient is None, (
        "wizard:adminPasswordTransient must not exist (FE-1)"
    )
    storage_dump = page.evaluate(
        "() => Object.fromEntries(Object.entries(window.sessionStorage))",
    )
    for k, v in storage_dump.items():
        assert secret not in (v or ""), (
            f"admin password leaked into sessionStorage[{k!r}] (FE-1)"
        )


def test_password_strength_indicator_hidden_when_empty(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    # No password typed — strength block must not render.
    assert page.locator(".password-strength").count() == 0


def test_password_strength_all_rules_pass(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "alice")
    page.fill("#admin-email", "alice@example.com")
    page.fill("#admin-password", "Hunter2-secure!")
    page.wait_for_selector(".password-strength", state="visible", timeout=2000)
    block = page.locator(".password-strength")
    assert "strength-strong" in (block.get_attribute("class") or "")
    # All three checks rendered green (.passed).
    assert page.locator(".password-strength-checks li.passed").count() == 3
    assert page.locator(".password-strength-checks li.unmet").count() == 0


def test_password_strength_rejects_short_numeric_similar(page, wizard_process):
    """A bad password fails all three client-side rules at once."""
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "alice")
    page.fill("#admin-email", "alice@example.com")
    # Short, all numeric — fails length and not_numeric.
    page.fill("#admin-password", "1234")
    page.wait_for_selector(".password-strength", state="visible", timeout=2000)
    length_li = page.locator("[data-check-id='length']")
    numeric_li = page.locator("[data-check-id='not_numeric']")
    assert "unmet" in (length_li.get_attribute("class") or "")
    assert "unmet" in (numeric_li.get_attribute("class") or "")
    # Bar should be in weak state.
    assert "strength-weak" in (
        page.locator(".password-strength").get_attribute("class") or ""
    )


def test_password_strength_flags_username_substring(page, wizard_process):
    """Password containing the username trips the similarity check."""
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-username", "alice")
    page.fill("#admin-email", "alice@example.com")
    page.fill("#admin-password", "alice-is-cool-9!")
    page.wait_for_selector(".password-strength", state="visible", timeout=2000)
    similar_li = page.locator("[data-check-id='not_similar']")
    assert "unmet" in (similar_li.get_attribute("class") or "")


def test_match_indicator_hidden_when_confirm_empty(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-password", "Hunter2-secure!")
    # Confirm not filled — no live indicator yet.
    assert page.locator(".password-match-indicator").count() == 0


def test_match_indicator_shows_mismatch_live(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-password", "Hunter2-secure!")
    page.fill("#admin-password-confirm", "different")
    page.wait_for_selector(
        ".password-match-indicator[data-match='false']",
        state="visible", timeout=2000,
    )
    indicator = page.locator(".password-match-indicator")
    assert "mismatched" in (indicator.get_attribute("class") or "")
    assert "matched" not in (indicator.get_attribute("class") or "").split()


def test_match_indicator_flips_to_match_live(page, wizard_process):
    """Typing the matching value flips mismatched → matched without a click."""
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    page.fill("#admin-password", "Hunter2-secure!")
    page.fill("#admin-password-confirm", "Hunter2-secur")  # one char short
    page.wait_for_selector(
        ".password-match-indicator[data-match='false']",
        state="visible", timeout=2000,
    )
    page.fill("#admin-password-confirm", "Hunter2-secure!")
    page.wait_for_selector(
        ".password-match-indicator[data-match='true']",
        state="visible", timeout=2000,
    )
    indicator = page.locator(".password-match-indicator")
    assert "matched" in (indicator.get_attribute("class") or "")
