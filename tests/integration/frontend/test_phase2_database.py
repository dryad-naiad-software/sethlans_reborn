# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) database page Playwright tests (FR-M2-4 / FR-FE-DB-DISCLOSURE)."""

from __future__ import annotations

import json

import pytest

from ._phase2_helpers import enter_token, mock_endpoint


def _arrive_at_database(page, wp):
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/topology", timeout=5000)
    page.locator("[data-choice-id='manager']").click()
    page.click("#topology-submit")
    page.wait_for_url(f"{wp.base_url}/network", timeout=5000)
    page.locator("[data-choice-id='no']").click()
    page.click("#network-submit")
    page.wait_for_url(f"{wp.base_url}/database", timeout=5000)


def test_sqlite_default_hides_credential_fields(page, wizard_process):
    """SQLite is the default; host/port/user/password fields are hidden."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    sqlite_card = page.locator("[data-choice-id='sqlite']")
    assert sqlite_card.get_attribute("aria-checked") == "true"
    # The credential subsection only renders when engine != 'sqlite'.
    assert page.locator("#db-host").count() == 0
    assert page.locator("#db-port").count() == 0
    assert page.locator("#db-user").count() == 0
    assert page.locator("#db-password").count() == 0


def test_postgresql_reveals_credential_fields(page, wizard_process):
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='postgresql']").click()
    page.wait_for_selector("#db-host", state="visible", timeout=2000)
    page.wait_for_selector("#db-port", state="visible")
    page.wait_for_selector("#db-user", state="visible")
    page.wait_for_selector("#db-password", state="visible")


def test_custom_engine_reveals_engine_path_field(page, wizard_process):
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='custom']").click()
    page.wait_for_selector("#db-engine-path", state="visible", timeout=2000)


def test_sqlite_happy_path_navigates_to_admin_user(page, wizard_process):
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.click("#db-submit")
    page.wait_for_url(f"{wp.base_url}/admin-user", timeout=5000)


@pytest.mark.parametrize("category,fragment", [
    ("auth_failed", "Authentication"),
    ("host_unreachable", "reach"),
    ("db_not_found", "not found"),
    ("permission_denied", "permission"),
    ("ssl_error", "SSL"),
    ("timeout", "timed out"),
])
def test_error_categories_render_user_friendly_message(
    page, wizard_process, category, fragment,
):
    """Each backend error category maps to a documented user-facing message."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    mock_endpoint(
        page,
        "**/api/wizard/database/",
        status=400,
        body={"error": category},
    )
    page.locator("[data-choice-id='postgresql']").click()
    page.fill("#db-host", "db.example.com")
    page.fill("#db-user", "u")
    page.fill("#db-password", "p")
    page.click("#db-submit")
    page.wait_for_selector(".alert-danger", state="visible", timeout=3000)
    msg = page.locator(".alert-danger").inner_text()
    assert fragment.lower() in msg.lower(), (category, msg)
    # No raw driver text should leak through.
    assert "Traceback" not in msg
    assert "psycopg" not in msg
    assert "pymysql" not in msg


def test_engine_value_in_post_body(page, wizard_process):
    wp = wizard_process
    _arrive_at_database(page, wp)
    captured: list[dict] = []

    def _capture(route):
        captured.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200, content_type="application/json", body="{}",
        )

    page.route("**/api/wizard/database/", _capture)
    page.locator("[data-choice-id='mysql']").click()
    page.fill("#db-host", "mysql.example.com")
    page.fill("#db-user", "u")
    page.fill("#db-password", "p")
    page.click("#db-submit")
    page.wait_for_url(f"{wp.base_url}/admin-user", timeout=5000)
    assert captured, "database endpoint never called"
    assert captured[0]["engine"] == "mysql", captured[0]
    assert captured[0]["host"] == "mysql.example.com", captured[0]


def test_password_not_stashed_in_form_state(page, wizard_process):
    """FR-CHK3-FORM-STATE — DB password is NEVER stashed in sessionStorage."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='postgresql']").click()
    page.fill("#db-host", "db.example.com")
    page.fill("#db-user", "myuser")
    page.fill("#db-password", "secret-password-do-not-stash")
    # Force a stash by selecting the engine (selectEngine calls _stash).
    page.locator("[data-choice-id='mysql']").click()
    raw = page.evaluate(
        "() => window.sessionStorage.getItem('wizard.form.database')",
    )
    assert raw is not None, "form state should be stashed"
    parsed = json.loads(raw)
    assert "password" not in parsed, parsed
    # Sanity: non-password fields ARE stashed.
    assert parsed.get("host") == "db.example.com", parsed
    assert parsed.get("user") == "myuser", parsed
