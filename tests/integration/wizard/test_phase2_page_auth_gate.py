# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Server-side page-level auth gate (issue #175).

Wizard page routes (``/``, ``/welcome``, ``/topology``, ``/network``,
``/database``, ``/admin-user``, ``/worker-password``, ``/verify``,
``/done``) are gated by the ``wizard_session`` cookie.
Unauthed GETs 302 to ``/token`` so first-time users never see a
wizard step page (and never the misleading "Your session expired"
flash on /token caused by the API gate kicking in mid-page).

These tests exercise the gate end-to-end against the live wizard
subprocess: they assert the redirect, the cookie set on auth success,
the page-serve when the cookie is present, the cookie invalidation
when a new auth POST rotates the session, and the static-asset
exemption.
"""

from __future__ import annotations

from . import _http
from ._phase1_session import open_session, session_cookie_header


GATED_PAGES = [
    "/",
    "/topology",
    "/network",
    "/database",
    "/admin-user",
    "/worker-password",
    "/verify",
    "/done",
]


# ---------------------------------------------------------------------
# Unauthed GETs redirect to /token
# ---------------------------------------------------------------------

def test_unauthed_root_redirects_to_token(wizard_process):
    wp = wizard_process
    status, headers, _ = _http.get(
        f"{wp.base_url}/",
        follow_redirects=False,
    )
    assert status == 302, status
    assert headers.get("Location") == "/token", headers
    # Defense in depth — 302 must not be cacheable, otherwise a back/
    # forward navigation post-auth would re-fire the gate from cache.
    assert headers.get("Cache-Control") == "no-store", headers


def test_unauthed_each_page_redirects_to_token(wizard_process):
    """Every gated route 302s to /token when the cookie is missing."""
    wp = wizard_process
    for path in GATED_PAGES:
        status, headers, _ = _http.get(
            f"{wp.base_url}{path}",
            follow_redirects=False,
        )
        assert status == 302, (path, status)
        assert headers.get("Location") == "/token", (path, headers)


def test_unauthed_with_invalid_cookie_redirects(wizard_process):
    """An invalid cookie value must NOT pass the gate."""
    wp = wizard_process
    status, headers, _ = _http.get(
        f"{wp.base_url}/topology",
        headers={"Cookie": "wizard_session=not-a-real-token"},
        follow_redirects=False,
    )
    assert status == 302, status
    assert headers.get("Location") == "/token"


# ---------------------------------------------------------------------
# /token and static assets are exempt
# ---------------------------------------------------------------------

def test_token_page_reachable_without_cookie(wizard_process):
    wp = wizard_process
    status, _, body = _http.get(
        f"{wp.base_url}/token",
        follow_redirects=False,
    )
    assert status == 200, status
    assert b"<title>Sethlans" in body


def test_redirecting_page_reachable_without_cookie(wizard_process):
    """``/redirecting`` is the post-handoff polite-shutdown page; the
    wizard has already cleared the session by the time the browser
    hits it, so it must NOT be gated (would always 302 in practice)."""
    wp = wizard_process
    status, _, body = _http.get(
        f"{wp.base_url}/redirecting",
        follow_redirects=False,
    )
    assert status == 200, status
    assert b"<title>Sethlans" in body


def test_static_assets_reachable_without_cookie(wizard_process):
    """CSS / JS / vendor assets bypass the page-auth gate (the gate
    is for the document responses; the assets themselves carry no
    sensitive content)."""
    wp = wizard_process
    for path in (
        "/static/vendor/petite-vue.js",
        "/static/css/wizard.css",
        "/static/js/common.js",
    ):
        status, _, _ = _http.get(
            f"{wp.base_url}{path}",
            follow_redirects=False,
        )
        assert status == 200, (path, status)


def test_health_endpoint_reachable_without_cookie(wizard_process):
    """``/api/health/`` is the cold-boot probe — never gated."""
    wp = wizard_process
    status, _, _ = _http.get(
        f"{wp.base_url}/api/health/",
        follow_redirects=False,
    )
    assert status == 200, status


# ---------------------------------------------------------------------
# Auth POST sets the cookie; page reachable after
# ---------------------------------------------------------------------

def test_auth_success_sets_session_cookie(wizard_process):
    """POST /api/wizard/auth/ on success returns Set-Cookie with
    Path=/, SameSite=Strict, no Max-Age (session cookie), no HttpOnly.

    The integration suite hits the wizard's plain-HTTP loopback
    listener directly (no Caddy in front), so Secure is intentionally
    omitted — issue #175 only applies Secure when the request reaches
    the wizard via Caddy with ``X-Forwarded-Proto: https``.
    """
    wp = wizard_process
    status, headers, body = _http.post_json(
        f"{wp.base_url}/api/wizard/auth/",
        {"token": wp.setup_token},
    )
    assert status == 200, body
    cookie = headers.get("Set-Cookie", "")
    assert cookie.startswith("wizard_session="), cookie
    # The cookie value should equal the JSON-returned session_token.
    assert body and body.get("session_token") in cookie, (cookie, body)
    # Required attributes.
    assert "Path=/" in cookie, cookie
    assert "SameSite=Strict" in cookie, cookie
    # Session cookie semantics: no Max-Age / Expires.
    assert "Max-Age" not in cookie, cookie
    assert "Expires=" not in cookie, cookie
    # HttpOnly explicitly NOT set so the JS can clear the cookie on
    # expireAndRedirect (and stale-cookie eviction on /token mount).
    assert "HttpOnly" not in cookie, cookie


def test_auth_success_marks_cookie_secure_with_forwarded_proto(wizard_process):
    """When Caddy forwards X-Forwarded-Proto: https, the cookie carries Secure."""
    wp = wizard_process
    status, headers, body = _http.post_json(
        f"{wp.base_url}/api/wizard/auth/",
        {"token": wp.setup_token},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert status == 200, body
    cookie = headers.get("Set-Cookie", "")
    assert "Secure" in cookie, cookie


def test_authed_page_serves_when_cookie_present(wizard_process):
    """After the cookie is set, GET /welcome serves the page."""
    wp = wizard_process
    session = open_session(wp)
    status, headers, body = _http.get(
        f"{wp.base_url}/",
        headers=session_cookie_header(session),
        follow_redirects=False,
    )
    assert status == 200, status
    assert headers.get("Content-Type", "").startswith("text/html")
    assert b"welcome.js" in body


def test_authed_each_page_serves(wizard_process):
    """Every gated page serves when the cookie is present."""
    wp = wizard_process
    session = open_session(wp)
    cookie = session_cookie_header(session)
    for path in GATED_PAGES:
        status, _, _ = _http.get(
            f"{wp.base_url}{path}",
            headers=cookie,
            follow_redirects=False,
        )
        assert status == 200, (path, status)


# ---------------------------------------------------------------------
# Cookie invalidation on session rotation
# ---------------------------------------------------------------------

def test_old_cookie_invalid_after_session_rotation(wizard_process):
    """A second auth POST rotates the session; the old cookie value
    must no longer pass the gate (single-active-session invariant)."""
    wp = wizard_process
    first = open_session(wp)
    # Second auth issues a fresh session and invalidates the first.
    second = open_session(wp)
    assert first != second
    # Old cookie → 302 to /token.
    status_old, headers_old, _ = _http.get(
        f"{wp.base_url}/topology",
        headers={"Cookie": f"wizard_session={first}"},
        follow_redirects=False,
    )
    assert status_old == 302, status_old
    assert headers_old.get("Location") == "/token"
    # New cookie → 200.
    status_new, _, _ = _http.get(
        f"{wp.base_url}/topology",
        headers={"Cookie": f"wizard_session={second}"},
        follow_redirects=False,
    )
    assert status_new == 200, status_new
