# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for issue #174 — ``expireAndRedirect`` target.

Phase 2 (commit ``4ccdfdd``) moved ``welcome.html`` to ``/`` and the
token-entry page (``index.html``) to ``/token``. The shared helper
``expireAndRedirect`` in ``wizard/frontend/static/js/common.js`` was
still hard-coded to redirect to ``/``, so any 401/403 from a wizard
API call bounced the user back to the welcome page — where the next
API call also 401'd and the user looped silently.

Three test families:

* :func:`test_expire_and_redirect_unit_navigates_to_token` —
  module-level invocation of ``expireAndRedirect`` from inside the
  wizard's own page context. Asserts the navigation target is
  ``/token``, the flash message survives, and the session token is
  cleared from ``sessionStorage``.

* :func:`test_welcome_next_without_token_lands_on_token_entry` —
  full-stack bounce regression. With no session token in storage,
  click the welcome page's Next button and assert the browser lands
  on ``/token`` (NOT looping back to ``/``) after exactly one
  navigation, with exactly one POST to ``/api/wizard/welcome/``.

* :func:`test_expire_and_redirect_from_each_page` and
  :func:`test_mount_time_pages_bounce_on_missing_token` — every
  Phase 2 wizard step page must, when its expire path is exercised
  with no token, land the user on ``/token``. Pages that POST
  automatically on mount (verify, done) are exercised by direct
  navigation; the other pages dynamically import the shared helper
  from their own origin to prove it ships the correct redirect
  target everywhere.
"""

from __future__ import annotations

import pytest

from ._phase2_helpers import enter_token


# ---------------------------------------------------------------------
# (a) Direct unit-style test of ``expireAndRedirect``.
# ---------------------------------------------------------------------

def test_expire_and_redirect_unit_navigates_to_token(page, wizard_process):
    """Import common.js inside a wizard page context and call the helper.

    The wizard pages serve common.js as an ES module from
    ``/static/js/common.js``; we drive it through Playwright's
    ``page.evaluate`` with a dynamic import so the test exercises the
    *real* shipped module — no copy-paste, no recompilation.

    Issue #175 — page routes are gated by the wizard_session cookie;
    we must pass through the auth flow before reaching the welcome
    page so the dynamic import can resolve against the right origin.
    """
    wp = wizard_process
    # Auth via the real flow so the wizard_session cookie is set; the
    # helper resolves to the welcome page (FR-CHK3-RESUME default).
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.wait_for_selector("#welcome-next")
    page.evaluate(
        "() => { window.sessionStorage.setItem("
        "'wizard:sessionToken', 'sentinel-value'); }"
    )

    # Dynamically import common.js and call expireAndRedirect.
    page.evaluate(
        """async () => {
            const mod = await import('/static/js/common.js');
            mod.expireAndRedirect('test-flash-message');
        }"""
    )

    # Wait for navigation to /token to settle.
    page.wait_for_url(f"{wp.base_url}/token", timeout=5000)
    assert page.url == f"{wp.base_url}/token"

    # Session token should be cleared.
    cleared = page.evaluate(
        "() => window.sessionStorage.getItem('wizard:sessionToken')",
    )
    assert cleared is None, cleared

    # Flash message should land on the token-entry page as an inline
    # error. ``auth.js`` calls ``consumeFlash()`` on DOMContentLoaded
    # which clears the storage entry, so the assertion lives on the
    # rendered DOM rather than on sessionStorage.
    error_locator = page.locator("#setup-token-error")
    error_locator.wait_for(state="visible", timeout=3000)
    assert "test-flash-message" in error_locator.inner_text()


# ---------------------------------------------------------------------
# (b) End-to-end bounce regression starting from the welcome page.
# ---------------------------------------------------------------------

def test_unauthed_root_redirects_to_token_with_no_loop(
    page, wizard_process,
):
    """Issue #175 — unauthed direct nav to ``/`` 302s to ``/token``.

    Pre-#175 the welcome page rendered, the user clicked Next, the
    POST 401'd and the user was bounced through ``expireAndRedirect``
    to ``/token`` with a misleading "session expired" flash. Post-#175
    the server-side page-auth gate redirects BEFORE the page renders,
    so the user never sees welcome and the welcome POST never fires.

    The original premise of this test (welcome → /token bounce) is
    now unreachable; we repurpose it to assert the redirect itself
    happens cleanly: exactly one navigation lands on ``/token``, and
    the welcome POST is NEVER fired (no loop, no stale page render).
    """
    wp = wizard_process

    # Track every top-level frame navigation so we can assert the
    # navigation count below.
    navigations: list[str] = []
    page.on("framenavigated", lambda f: (
        navigations.append(f.url) if f == page.main_frame else None
    ))

    # Track POSTs to /api/wizard/welcome/ — must NEVER fire under #175.
    welcome_posts: list[str] = []

    def _on_request(request):
        if "/api/wizard/welcome/" in request.url and request.method == "POST":
            welcome_posts.append(request.url)

    page.on("request", _on_request)

    # Direct nav to ``/`` without a wizard_session cookie. The server-
    # side gate 302s to ``/token``; the browser follows transparently.
    page.goto(f"{wp.base_url}/")
    page.wait_for_url(f"{wp.base_url}/token", timeout=5000)
    assert page.url == f"{wp.base_url}/token"

    # The browser must have landed on /token exactly once.
    token_landings = [u for u in navigations if u.endswith("/token")]
    assert len(token_landings) == 1, (
        f"Expected exactly one navigation to /token, got {token_landings}"
    )

    # The welcome POST must NEVER have fired — the gate redirects
    # before welcome.html serves, so welcome.js never runs.
    assert welcome_posts == [], welcome_posts


# ---------------------------------------------------------------------
# (c) Cross-page regression — the same shared helper ships to every
# Phase 2 wizard step. We assert that proposition two ways:
#
#  * Pages that POST automatically on mount (``/verify``, ``/done``)
#    are exercised by direct navigation: arriving on the page with no
#    session token causes the page's own expire path to run, and the
#    browser must end up on ``/token``.
#
#  * The other Phase 2 step pages (``/topology``, ``/network``,
#    ``/database``, ``/admin-user``, ``/worker-password``, ``/ffmpeg``)
#    do not POST until the user submits a form. We don't try to
#    synthesise valid form state for every page — instead, we land on
#    the page, dynamically import the shipped ``common.js`` from the
#    page's origin, and call ``expireAndRedirect`` directly. If the
#    common.js URL ever diverges per page, this test catches it.
# ---------------------------------------------------------------------

PAGES_THAT_POST_ON_MOUNT = [
    pytest.param("/ffmpeg", id="ffmpeg"),
    pytest.param("/verify", id="verify"),
    pytest.param("/done", id="done"),
]

PAGES_WITH_SUBMIT_FORMS = [
    pytest.param("/topology", id="topology"),
    pytest.param("/network", id="network"),
    pytest.param("/database", id="database"),
    pytest.param("/admin-user", id="admin-user"),
    pytest.param("/worker-password", id="worker-password"),
]


@pytest.mark.parametrize("path", PAGES_THAT_POST_ON_MOUNT)
def test_mount_time_pages_bounce_on_missing_token(
    page, wizard_process, path,
):
    """Pages that POST on mount must redirect to /token without a token.

    Issue #175 — the server-side page-auth gate now 302s before the
    page even renders, so the mount-time POST never fires. The
    end-state assertion (browser lands on /token) still holds, just
    via the gate instead of via the JS expire path.
    """
    wp = wizard_process

    page.goto(f"{wp.base_url}{path}")
    page.wait_for_url(f"{wp.base_url}/token", timeout=10000)
    assert page.url == f"{wp.base_url}/token", page.url


@pytest.mark.parametrize("path", PAGES_WITH_SUBMIT_FORMS)
def test_expire_and_redirect_from_each_page(page, wizard_process, path):
    """Importing common.js from any Phase 2 page redirects to /token.

    Every wizard step page imports common.js via
    ``import { expireAndRedirect } from '/static/js/common.js'``. This
    test authenticates first (issue #175 — pages are gated by the
    wizard_session cookie), lands on each page, then dynamically
    imports the shipped module from the same origin and asserts the
    helper navigates to ``/token``.
    """
    wp = wizard_process
    # Pass through the auth flow so the page-auth gate lets us reach
    # the target page; expireAndRedirect's behaviour is unchanged.
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/")
    page.goto(f"{wp.base_url}{path}")
    page.wait_for_load_state("domcontentloaded")

    page.evaluate(
        """async () => {
            const mod = await import('/static/js/common.js');
            mod.expireAndRedirect('expired');
        }"""
    )
    page.wait_for_url(f"{wp.base_url}/token", timeout=5000)
    assert page.url == f"{wp.base_url}/token"

    cleared = page.evaluate(
        "() => window.sessionStorage.getItem('wizard:sessionToken')",
    )
    assert cleared is None, cleared
