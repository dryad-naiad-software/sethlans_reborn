# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 wizard chrome tests (issue #179).

Coverage for the framing UI overhaul shipped on top of the per-step
pages: the horizontal stepper in the card header, Back navigation in
the card footer, and the password mask/unmask eye-toggle on the admin
and worker-password pages.

These tests drive the *real* wizard subprocess via Playwright (the
same fixture chain Phase 2's other ``test_phase2_*`` modules use).
"""

from __future__ import annotations

from ._phase2_helpers import (
    enter_token,
    write_progress_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _land_via_resume(page, wp, route, *, topology="manager"):
    """Pre-populate progress + topology, then resume to *route*."""
    checkpoints = {
        "/topology": ["welcome_seen"],
        "/network": ["welcome_seen", "topology_chosen"],
        "/database": [
            "welcome_seen", "topology_chosen", "network_configured",
        ],
        "/admin-user": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured",
        ],
        "/worker-password": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured", "admin_validated",
        ],
        "/ffmpeg": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured", "admin_validated",
        ],
        "/verify": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured", "admin_validated", "ffmpeg_installed",
        ],
    }
    cps = checkpoints.get(route, [])
    if cps:
        write_progress_json(wp.data_dir, cps, topology=topology)
    enter_token(page, wp.base_url, wp.setup_token, expect_url=route)
    # Wait for the chrome to mount and apply topology context.
    page.wait_for_function(
        "() => document.querySelectorAll('.wizard-stepper-item').length > 0",
        timeout=5000,
    )


def _stepper_labels(page) -> list[str]:
    return [
        el.strip()
        for el in page.locator(".wizard-stepper-label").all_inner_texts()
    ]


def _wait_for_step_count(page, count, timeout=5000):
    """Wait until the rendered stepper has exactly *count* items.

    The chrome's stepper starts with a manager-flavoured fallback (7
    entries) and re-renders to manager_worker (8 entries) once the
    /resume-target probe completes. Tests that need a specific count
    must wait for the right count to settle.
    """
    page.wait_for_function(
        "(n) => document.querySelectorAll('.wizard-stepper-item').length === n",
        arg=count,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Stepper — topology-aware visibility
# ---------------------------------------------------------------------------

def test_manager_topology_shows_seven_steps(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/network", topology="manager")
    _wait_for_step_count(page, 7)
    labels = _stepper_labels(page)
    assert labels == [
        "Topology", "Network", "Database", "Admin",
        "FFmpeg", "Verify", "Done",
    ], labels


def test_manager_worker_topology_shows_eight_steps(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/network", topology="manager_worker")
    _wait_for_step_count(page, 8)
    labels = _stepper_labels(page)
    assert labels == [
        "Topology", "Network", "Database", "Admin", "Worker",
        "FFmpeg", "Verify", "Done",
    ], labels


def test_stepper_has_aria_label(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/network", topology="manager")
    container = page.locator(".wizard-stepper")
    assert container.get_attribute("aria-label") == "Setup progress"


def test_active_step_has_aria_current(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/database", topology="manager")
    _wait_for_step_count(page, 7)
    # Database is the third step (index 2).
    items = page.locator(".wizard-stepper-item")
    # Exactly one item carries aria-current="step".
    current = page.locator(".wizard-stepper-item[aria-current='step']")
    assert current.count() == 1, current.count()
    # And that item is the Database item.
    label = current.locator(".wizard-stepper-label").inner_text().strip()
    assert label == "Database", label
    # The is-current class also reflects the current state.
    assert "is-current" in (items.nth(2).get_attribute("class") or "")


def test_completed_steps_have_is_completed_class(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/database", topology="manager")
    _wait_for_step_count(page, 7)
    items = page.locator(".wizard-stepper-item")
    # Topology + Network are completed (we have those checkpoints).
    cls0 = items.nth(0).get_attribute("class") or ""
    cls1 = items.nth(1).get_attribute("class") or ""
    assert "is-completed" in cls0, cls0
    assert "is-completed" in cls1, cls1
    # FFmpeg / Verify / Done are NOT completed.
    for idx in (4, 5, 6):
        cls = items.nth(idx).get_attribute("class") or ""
        assert "is-completed" not in cls, (idx, cls)
        assert "is-current" not in cls, (idx, cls)


# ---------------------------------------------------------------------------
# Back navigation
# ---------------------------------------------------------------------------

def test_topology_page_has_no_back_button(page, wizard_process):
    """Topology is the first stepper entry — no Back button rendered."""
    wp = wizard_process
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    page.wait_for_function(
        "() => document.querySelectorAll('.wizard-stepper-item').length > 0",
        timeout=5000,
    )
    # The footer exists, but no element with id ending in "-back" lives
    # on the topology page.
    assert page.locator("button[id$='-back']").count() == 0


def test_back_from_database_returns_to_network(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/database", topology="manager")
    page.locator("#db-back").click()
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)


def test_back_from_database_restores_network_form_state(page, wizard_process):
    """FR-CHK3-FORM-STATE — Back from /database to /network repopulates
    the previously stashed Yes/No selection and bind port.
    """
    wp = wizard_process
    # Drive forward to /database via the live flow so the network stash
    # gets written by the network page's _stash() call.
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    page.wait_for_selector("[data-choice-id='manager']", timeout=5000)
    page.locator("[data-choice-id='manager']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    page.wait_for_selector("[data-choice-id='yes']", timeout=5000)
    page.locator("[data-choice-id='yes']").click()
    page.click("#network-submit")
    page.wait_for_url(f"{wp.base_url}/database", timeout=5000)
    # Now click Back; we should land on /network with Yes pre-selected.
    page.wait_for_selector("#db-back", timeout=5000)
    page.locator("#db-back").click()
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    page.wait_for_function(
        "() => {"
        "const c = document.querySelector(\"[data-choice-id='yes']\");"
        "return c && c.getAttribute('aria-checked') === 'true';"
        "}",
        timeout=5000,
    )


def test_back_button_visible_on_intermediate_pages(page, wizard_process):
    """Network, Database, Admin, FFmpeg, Verify all render a Back button."""
    wp = wizard_process
    cases = [
        ("/network", "#network-back"),
        ("/database", "#db-back"),
        ("/admin-user", "#admin-back"),
        ("/ffmpeg", "#ffmpeg-back"),
    ]
    for route, btn_id in cases:
        _land_via_resume(page, wp, route, topology="manager")
        assert page.locator(btn_id).count() == 1, (route, btn_id)


# ---------------------------------------------------------------------------
# Eye-toggle (password mask/unmask)
# ---------------------------------------------------------------------------

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


def test_admin_password_eye_toggle_flips_input_type(page, wizard_process):
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    pw = page.locator("#admin-password")
    btn = page.locator("#admin-password-toggle")
    # Default — masked.
    assert pw.get_attribute("type") == "password"
    assert btn.get_attribute("aria-pressed") == "false"
    assert btn.get_attribute("aria-label") == "Show password"
    btn.click()
    page.wait_for_function(
        "() => document.querySelector('#admin-password').type === 'text'",
        timeout=2000,
    )
    assert pw.get_attribute("type") == "text"
    assert btn.get_attribute("aria-pressed") == "true"
    assert btn.get_attribute("aria-label") == "Hide password"
    btn.click()
    page.wait_for_function(
        "() => document.querySelector('#admin-password').type === 'password'",
        timeout=2000,
    )
    assert pw.get_attribute("type") == "password"
    assert btn.get_attribute("aria-pressed") == "false"


def test_admin_password_toggles_are_linked(page, wizard_process):
    """Clicking either eye flips BOTH password fields together.

    Both fields share a single showPassword state so the user can
    verify password and confirm match without an extra click.
    """
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    pw = page.locator("#admin-password")
    pw_confirm = page.locator("#admin-password-confirm")
    btn_pw = page.locator("#admin-password-toggle")
    btn_confirm = page.locator("#admin-password-confirm-toggle")
    # Click password eye — both inputs flip to text.
    btn_pw.click()
    page.wait_for_function(
        "() => document.querySelector('#admin-password').type === 'text'"
        " && document.querySelector('#admin-password-confirm').type === 'text'",
        timeout=2000,
    )
    assert pw.get_attribute("type") == "text"
    assert pw_confirm.get_attribute("type") == "text"
    assert btn_pw.get_attribute("aria-pressed") == "true"
    assert btn_confirm.get_attribute("aria-pressed") == "true"
    # Click confirm eye — both flip back to password.
    btn_confirm.click()
    page.wait_for_function(
        "() => document.querySelector('#admin-password').type === 'password'"
        " && document.querySelector('#admin-password-confirm').type === 'password'",
        timeout=2000,
    )
    assert pw.get_attribute("type") == "password"
    assert pw_confirm.get_attribute("type") == "password"
    assert btn_pw.get_attribute("aria-pressed") == "false"
    assert btn_confirm.get_attribute("aria-pressed") == "false"


def test_password_eye_toggles_skipped_in_tab_order(page, wizard_process):
    """Tab from password jumps to confirm-password, not the eye toggle.

    The eye toggle is mouse/touch operable but lives outside the natural
    tab path so password-manager UX (Tab between fields, Tab to Continue)
    isn't interrupted.
    """
    wp = wizard_process
    _arrive_at_admin_user(page, wp)
    assert (
        page.locator("#admin-password-toggle").get_attribute("tabindex")
        == "-1"
    )
    assert (
        page.locator("#admin-password-confirm-toggle").get_attribute("tabindex")
        == "-1"
    )
    # Functional sanity: focus password, press Tab, focus lands on confirm.
    page.locator("#admin-password").focus()
    page.keyboard.press("Tab")
    page.wait_for_function(
        "() => document.activeElement.id === 'admin-password-confirm'",
        timeout=2000,
    )


def test_worker_password_eye_toggle_flips_input_type(page, wizard_process):
    wp = wizard_process
    _land_via_resume(page, wp, "/worker-password", topology="manager_worker")
    page.wait_for_selector("#use-admin-checkbox", state="visible")
    # Uncheck so the password field is rendered.
    page.click("#use-admin-checkbox")
    page.wait_for_selector("#worker-password", state="visible", timeout=3000)
    pw = page.locator("#worker-password")
    btn = page.locator("#worker-password-toggle")
    assert pw.get_attribute("type") == "password"
    assert btn.get_attribute("aria-pressed") == "false"
    assert btn.get_attribute("aria-label") == "Show password"
    btn.click()
    page.wait_for_function(
        "() => document.querySelector('#worker-password').type === 'text'",
        timeout=2000,
    )
    assert pw.get_attribute("type") == "text"
    assert btn.get_attribute("aria-pressed") == "true"
    assert btn.get_attribute("aria-label") == "Hide password"
