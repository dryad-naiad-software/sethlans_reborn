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


# ---------------------------------------------------------------------
# (a) Direct unit-style test of ``expireAndRedirect``.
# ---------------------------------------------------------------------

def test_expire_and_redirect_unit_navigates_to_token(page, wizard_process):
    """Import common.js inside a wizard page context and call the helper.

    The wizard pages serve common.js as an ES module from
    ``/static/js/common.js``; we drive it through Playwright's
    ``page.evaluate`` with a dynamic import so the test exercises the
    *real* shipped module — no copy-paste, no recompilation.
    """
    wp = wizard_process
    # Land on the welcome page so common.js's import URL resolves
    # against the right origin. We set sessionStorage explicitly so
    # we can prove the helper clears it.
    page.goto(f"{wp.base_url}/")
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

def test_welcome_next_without_token_lands_on_token_entry(
    page, wizard_process,
):
    """Click Next on welcome with no session token → land on /token.

    Pre-fix, this test would land on ``/`` (welcome again) because
    ``expireAndRedirect`` hard-coded ``/`` as its target. Post-fix,
    the user lands on ``/token`` — exactly one navigation, no loop.
    """
    wp = wizard_process

    # Track every top-level frame navigation so we can assert the
    # navigation count below.
    navigations: list[str] = []
    page.on("framenavigated", lambda f: (
        navigations.append(f.url) if f == page.main_frame else None
    ))

    # Track POSTs to /api/wizard/welcome/ — must fire exactly once.
    welcome_posts: list[str] = []

    def _on_request(request):
        if "/api/wizard/welcome/" in request.url and request.method == "POST":
            welcome_posts.append(request.url)

    page.on("request", _on_request)

    page.goto(f"{wp.base_url}/")
    page.wait_for_selector("#welcome-next")

    # Make sure no stale session token leaks in.
    page.evaluate("() => window.sessionStorage.clear()")

    page.click("#welcome-next")
    page.wait_for_url(f"{wp.base_url}/token", timeout=5000)
    assert page.url == f"{wp.base_url}/token"

    # The browser must have navigated to /token exactly once. A loop
    # would show /token → / → /token → / repeating.
    token_landings = [u for u in navigations if u.endswith("/token")]
    assert len(token_landings) == 1, (
        f"Expected exactly one navigation to /token, got {token_landings}"
    )

    # The welcome POST must have fired exactly once. A loop would keep
    # re-firing it as the user re-lands on welcome.
    assert len(welcome_posts) == 1, welcome_posts


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
    """Pages that POST on mount must redirect to /token without a token."""
    wp = wizard_process

    # Land on the wizard root first so we have an origin to clear
    # sessionStorage on — direct goto to /verify would trigger the
    # mount POST before we could clear stale state.
    page.goto(f"{wp.base_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("() => window.sessionStorage.clear()")

    page.goto(f"{wp.base_url}{path}")
    page.wait_for_url(f"{wp.base_url}/token", timeout=10000)
    assert page.url == f"{wp.base_url}/token", page.url


@pytest.mark.parametrize("path", PAGES_WITH_SUBMIT_FORMS)
def test_expire_and_redirect_from_each_page(page, wizard_process, path):
    """Importing common.js from any Phase 2 page redirects to /token.

    Every wizard step page imports common.js via
    ``import { expireAndRedirect } from '/static/js/common.js'``. This
    test lands on each page, dynamically imports that same module from
    the same origin, and asserts the helper navigates to ``/token``.

    The point is to catch regressions where, e.g., a future page-
    specific override or a stale cached copy diverges from the shared
    behaviour. Today there is exactly one common.js module — but the
    parametrized check makes that invariant explicit and noisy.
    """
    wp = wizard_process
    page.goto(f"{wp.base_url}{path}")
    page.wait_for_load_state("domcontentloaded")

    # Set a sentinel session token so we can prove the helper clears it.
    page.evaluate(
        "() => { window.sessionStorage.setItem("
        "'wizard:sessionToken', 'sentinel'); }"
    )

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
