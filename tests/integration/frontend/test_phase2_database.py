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
    """SQLite hides db-name + host/port/user/password — default path is implicit."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    sqlite_card = page.locator("[data-choice-id='sqlite']")
    assert sqlite_card.get_attribute("aria-checked") == "true"
    # db-name and the credential subsection only render when engine != sqlite.
    assert page.locator("#db-name").count() == 0
    assert page.locator("#db-host").count() == 0
    assert page.locator("#db-port").count() == 0
    assert page.locator("#db-user").count() == 0
    assert page.locator("#db-password").count() == 0


def test_postgresql_reveals_credential_fields(page, wizard_process):
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='postgresql']").click()
    page.wait_for_selector("#db-name", state="visible", timeout=2000)
    page.wait_for_selector("#db-host", state="visible")
    page.wait_for_selector("#db-port", state="visible")
    page.wait_for_selector("#db-user", state="visible")
    page.wait_for_selector("#db-password", state="visible")


def test_custom_engine_reveals_engine_path_field(page, wizard_process):
    """Custom is hidden behind Show advanced options; expand then click."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    # Default view does NOT expose Custom — verify, then expand.
    assert page.locator("[data-choice-id='custom']").count() == 0
    page.click("#db-advanced-toggle")
    page.wait_for_selector("[data-choice-id='custom']", state="visible", timeout=2000)
    page.locator("[data-choice-id='custom']").click()
    page.wait_for_selector("#db-engine-path", state="visible", timeout=2000)


def test_advanced_toggle_collapses_back_to_sqlite(page, wizard_process):
    """Collapsing while Custom is selected snaps engine back to sqlite."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.click("#db-advanced-toggle")
    page.wait_for_selector("[data-choice-id='custom']", state="visible", timeout=2000)
    page.locator("[data-choice-id='custom']").click()
    custom = page.locator("[data-choice-id='custom']")
    assert custom.get_attribute("aria-checked") == "true"
    # Collapse — custom card disappears AND engine drops back to sqlite.
    page.click("#db-advanced-toggle")
    page.wait_for_selector("[data-choice-id='custom']", state="hidden", timeout=2000)
    sqlite = page.locator("[data-choice-id='sqlite']")
    assert sqlite.get_attribute("aria-checked") == "true"


def test_advanced_toggle_aria_expanded_reflects_state(page, wizard_process):
    """The toggle button advertises its expanded/collapsed state via ARIA."""
    wp = wizard_process
    _arrive_at_database(page, wp)
    toggle = page.locator("#db-advanced-toggle")
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    page.wait_for_function(
        "() => document.querySelector('#db-advanced-toggle')"
        ".getAttribute('aria-expanded') === 'true'",
        timeout=2000,
    )


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


def test_radiogroup_fieldset_has_no_aria_expanded(page, wizard_process):
    """FE-2 regression — ``aria-expanded`` is not a valid property on
    ``role=radiogroup``. Removing it stops axe-core from flagging the
    fieldset under the supported-roles rule. The disclosure-style
    announcement still happens via the existing ``aria-live=polite``
    region adjacent to the credentials block.
    """
    wp = wizard_process
    _arrive_at_database(page, wp)
    fieldset = page.locator(".db-engine-group")
    # SQLite (default): aria-expanded must NOT be present.
    assert fieldset.get_attribute("aria-expanded") is None
    # Switch engine to mysql (would have triggered :aria-expanded='true'
    # under the old binding). Still must not appear.
    page.locator("[data-choice-id='mysql']").click()
    page.wait_for_function(
        "() => {"
        "const f = document.querySelector('.db-engine-group');"
        "return f && f.getAttribute('aria-checked') === null;"
        "}",
        timeout=2000,
    )
    assert fieldset.get_attribute("aria-expanded") is None


def test_port_resets_to_engine_default_on_swap(page, wizard_process):
    """FE-5 regression — switching engines snaps the port to the new
    engine's canonical default. The previous bug stashed the prior
    engine's port (e.g., 5432 from postgresql) and overrode the new
    engine's default (3306 for mysql).
    """
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='postgresql']").click()
    page.wait_for_selector("#db-port", state="visible", timeout=2000)
    pg_port = page.locator("#db-port").input_value()
    assert pg_port == "5432", f"PostgreSQL default port should be 5432, got {pg_port}"
    page.locator("[data-choice-id='mysql']").click()
    # Port input is the same DOM element under v-show, but the value
    # should have updated reactively to the mysql default.
    page.wait_for_function(
        "() => document.querySelector('#db-port').value === '3306'",
        timeout=2000,
    )
    mysql_port = page.locator("#db-port").input_value()
    assert mysql_port == "3306", (
        f"MySQL default port should be 3306 after swap, got {mysql_port} "
        "(the FE-5 stale-port-after-swap bug regressed)."
    )


def test_port_not_stashed_in_form_state(page, wizard_process):
    """FE-5 regression — port is recomputed from engine on load and
    therefore is NOT stashed in sessionStorage. Stashing port across
    engine swaps is the bug FE-5 prevents from regressing.
    """
    wp = wizard_process
    _arrive_at_database(page, wp)
    page.locator("[data-choice-id='postgresql']").click()
    page.wait_for_selector("#db-port", state="visible", timeout=2000)
    raw = page.evaluate(
        "() => window.sessionStorage.getItem('wizard.form.database')",
    )
    assert raw is not None, "form state should be stashed"
    parsed = json.loads(raw)
    assert "port" not in parsed, (
        f"port should NOT be in stash (FE-5 fix); got: {parsed}"
    )


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
