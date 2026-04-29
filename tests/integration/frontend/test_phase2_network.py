# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) network page Playwright tests (FR-M2-3)."""

from __future__ import annotations

import json

from ._phase2_helpers import enter_token, mock_endpoint


def _arrive_at_network(page, wp):
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    # Wait for topology cards to mount before clicking.
    page.wait_for_selector("[data-choice-id='manager']", timeout=5000)
    page.locator("[data-choice-id='manager']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    # Wait for network cards to mount (Petite-vue mounts on DOMContentLoaded).
    page.wait_for_selector("[data-choice-id='yes']", timeout=5000)


def test_yes_no_radiogroup_renders(page, wizard_process):
    wp = wizard_process
    _arrive_at_network(page, wp)
    cards = page.locator("[role='radio']")
    assert cards.count() == 2
    # Continue is disabled until a choice is made.
    assert page.locator("#network-submit").get_attribute("aria-disabled") == "true"


def test_aria_controls_target_exists_when_collapsed(page, wizard_process):
    """FE-3 regression — ``aria-controls='network-advanced'`` references
    ``#network-advanced``. The element must exist in the DOM whether the
    advanced section is open or closed (the previous ``v-if`` made the
    target disappear when collapsed, breaking the aria-controls contract
    for assistive tech).
    """
    wp = wizard_process
    _arrive_at_network(page, wp)
    # Collapsed by default: the controlled element must still exist.
    page.wait_for_function(
        "() => {"
        "const b = document.querySelector("
        "\"button[aria-controls='network-advanced']\");"
        "return b && b.getAttribute('aria-expanded') === 'false';"
        "}",
        timeout=5000,
    )
    assert page.locator("#network-advanced").count() == 1, (
        "FE-3: #network-advanced must exist in the DOM when the toggle "
        "is collapsed (v-show, not v-if)."
    )
    # And after opening, still exactly one (no double-mount).
    page.locator("button[aria-controls='network-advanced']").click()
    page.wait_for_function(
        "() => {"
        "const b = document.querySelector("
        "\"button[aria-controls='network-advanced']\");"
        "return b && b.getAttribute('aria-expanded') === 'true';"
        "}",
        timeout=2000,
    )
    assert page.locator("#network-advanced").count() == 1, (
        "FE-3: #network-advanced must still be exactly one element after open."
    )


def test_advanced_disclosure_reveals_fields(page, wizard_process):
    wp = wizard_process
    _arrive_at_network(page, wp)
    advanced_btn = page.locator("button[aria-controls='network-advanced']")
    # Wait for Petite-vue to set aria-expanded='false' on initial mount.
    page.wait_for_function(
        "() => {"
        "const b = document.querySelector("
        "\"button[aria-controls='network-advanced']\");"
        "return b && b.getAttribute('aria-expanded') === 'false';"
        "}",
        timeout=5000,
    )
    advanced_btn.click()
    page.wait_for_function(
        "() => {"
        "const b = document.querySelector("
        "\"button[aria-controls='network-advanced']\");"
        "return b && b.getAttribute('aria-expanded') === 'true';"
        "}",
        timeout=2000,
    )
    page.wait_for_selector("#bind-port", state="visible")
    page.wait_for_selector("#data-dir", state="visible")


def test_happy_path_navigates_to_database(page, wizard_process):
    wp = wizard_process
    _arrive_at_network(page, wp)
    page.locator("[data-choice-id='no']").click()
    page.click("#network-submit")
    page.wait_for_url(f"{wp.base_url}/database", timeout=5000)


def test_invalid_data_dir_shows_inline_error(page, wizard_process):
    """Backend rejects /etc as data_dir; the JS surfaces the error msg."""
    wp = wizard_process
    _arrive_at_network(page, wp)
    page.locator("[data-choice-id='no']").click()
    page.locator("button[aria-controls='network-advanced']").click()
    page.fill("#data-dir", "/etc/sethlans")
    page.click("#network-submit")
    # Inline error region renders. URL stays /network.
    page.wait_for_selector("[role='alert']", state="visible", timeout=3000)
    error_text = page.locator(".alert-danger").inner_text()
    assert "data" in error_text.lower() or "directory" in error_text.lower()
    assert page.url.endswith("/network")


def test_bind_failed_response_renders_message(page, wizard_process):
    """Mock the backend to return ``error: bind_failed``."""
    wp = wizard_process
    _arrive_at_network(page, wp)
    mock_endpoint(
        page,
        "**/api/wizard/network/",
        status=400,
        body={"error": "bind_failed"},
    )
    page.locator("[data-choice-id='no']").click()
    page.click("#network-submit")
    page.wait_for_selector(".alert-danger", state="visible", timeout=3000)
    msg = page.locator(".alert-danger").inner_text()
    assert "bind" in msg.lower() or "port" in msg.lower(), msg
    assert page.url.endswith("/network")


def test_form_state_stash_survives_reload(page, wizard_process):
    """Yes/No selection persists across page reload (FR-CHK3-FORM-STATE).

    The disclosure-open state is UI state, not form state, and is not
    asserted here. The Yes/No radio choice IS form state and the
    sessionStorage stash repopulates it on mount.
    """
    wp = wizard_process
    _arrive_at_network(page, wp)
    page.locator("[data-choice-id='yes']").click()
    # Reload — sessionStorage survives; loadFormState repopulates.
    page.reload()
    page.wait_for_selector("[data-choice-id='yes']", timeout=5000)
    page.wait_for_function(
        "() => {"
        "const c = document.querySelector(\"[data-choice-id='yes']\");"
        "return c && c.getAttribute('aria-checked') === 'true';"
        "}",
        timeout=5000,
    )
    yes_card = page.locator("[data-choice-id='yes']")
    assert yes_card.get_attribute("aria-checked") == "true"


def test_form_state_stash_writes_session_storage(page, wizard_process):
    """Submit triggers _stash(); sessionStorage contains the bind port.

    Asserts the stash mechanic via the storage API directly — the
    UI-side restore is covered by
    :func:`test_form_state_stash_survives_reload`. This test pins the
    contract that the WRITE side puts the right values in storage.
    """
    wp = wizard_process
    _arrive_at_network(page, wp)
    page.locator("[data-choice-id='no']").click()
    page.locator("button[aria-controls='network-advanced']").click()
    page.wait_for_selector("#bind-port", state="visible")
    page.fill("#bind-port", "9999")
    mock_endpoint(
        page,
        "**/api/wizard/network/",
        status=400,
        body={"error": "bind_failed"},
    )
    page.click("#network-submit")
    page.wait_for_selector(".alert-danger", state="visible", timeout=3000)
    raw = page.evaluate(
        "() => window.sessionStorage.getItem('wizard.form.network')",
    )
    assert raw is not None, "form state not stashed"
    parsed = json.loads(raw)
    assert parsed.get("bindPort") == 9999, parsed
    assert parsed.get("allowExternal") is False, parsed


def test_yes_choice_sends_zero_zero_zero_zero_host(page, wizard_process):
    """The Yes radio maps to bind_host=0.0.0.0 in the POST body."""
    wp = wizard_process
    _arrive_at_network(page, wp)

    captured: list[dict] = []

    def _capture(route):
        request = route.request
        captured.append(json.loads(request.post_data or "{}"))
        route.fulfill(
            status=200, content_type="application/json", body="{}",
        )

    page.route("**/api/wizard/network/", _capture)
    page.locator("[data-choice-id='yes']").click()
    page.click("#network-submit")
    page.wait_for_url(f"{wp.base_url}/database", timeout=5000)
    assert captured, "network endpoint was never called"
    assert captured[0]["bind_host"] == "0.0.0.0", captured[0]
