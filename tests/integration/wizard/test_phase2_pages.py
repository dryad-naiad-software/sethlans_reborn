# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Phase 2 (Spec 2) frontend pages — smoke + handler integration tests.

Covers the new HTML pages added under ``wizard/frontend/static/`` and
the two new backend endpoints (``/api/wizard/welcome/`` and
``/api/wizard/resume-target/``) introduced for the FR-CHK3-RESUME flow.
"""

from __future__ import annotations

from . import _http
from ._phase1_session import (
    open_session,
    session_cookie_header,
    session_headers,
)


# ---------------------------------------------------------------------
# Page-serve smoke tests
# ---------------------------------------------------------------------

PHASE2_PAGES = [
    ("/", b"welcome.js"),
    ("/token", b"auth.js"),
    ("/network", b"network.js"),
    ("/database", b"database.js"),
    ("/admin-user", b"admin_user.js"),
    ("/worker-password", b"worker_password.js"),
    ("/ffmpeg", b"ffmpeg.js"),
    ("/verify", b"verify.js"),
    ("/done", b"done.js"),
]


def test_each_phase2_page_serves_html(wizard_process):
    """Every wizard page serves its HTML when the session cookie is present.

    Issue #175 — page routes are gated server-side; the test
    authenticates first and passes the wizard_session cookie. The
    ``/token`` page is exempt and reachable without the cookie.
    """
    wp = wizard_process
    session = open_session(wp)
    cookie = session_cookie_header(session)
    for path, marker in PHASE2_PAGES:
        # /token is reachable without the cookie (entry point).
        hdrs = None if path == "/token" else cookie
        status, headers, body = _http.get(f"{wp.base_url}{path}", headers=hdrs)
        assert status == 200, (path, status)
        assert headers.get("Content-Type", "").startswith("text/html"), path
        assert marker in body, (
            f"page {path} does not load its module script ({marker!r})"
        )


def test_phase2_pages_carry_csp_headers(wizard_process):
    """Every new HTML page MUST carry the Spec 1 CSP header
    unchanged (FR-VENDOR2)."""
    wp = wizard_process
    session = open_session(wp)
    cookie = session_cookie_header(session)
    for path, _marker in PHASE2_PAGES:
        hdrs = None if path == "/token" else cookie
        _, headers, _ = _http.get(f"{wp.base_url}{path}", headers=hdrs)
        csp = headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp, path
        assert "frame-ancestors 'none'" in csp, path


def test_phase2_pages_have_noscript_block(wizard_process):
    """FR-VENDOR3 — every new HTML page has a <noscript> block."""
    wp = wizard_process
    session = open_session(wp)
    cookie = session_cookie_header(session)
    for path, _ in PHASE2_PAGES:
        hdrs = None if path == "/token" else cookie
        _, _, body = _http.get(f"{wp.base_url}{path}", headers=hdrs)
        assert b"<noscript>" in body, path


# ---------------------------------------------------------------------
# Welcome handler smoke
# ---------------------------------------------------------------------

def test_welcome_endpoint_writes_checkpoint(wizard_process):
    wp = wizard_process
    session = open_session(wp)
    status, _, body = _http.post_json(
        f"{wp.base_url}/api/wizard/welcome/",
        {},
        headers=session_headers(session),
    )
    assert status == 200, body
    assert body == {"status": "ok"}


def test_welcome_endpoint_requires_session(wizard_process):
    wp = wizard_process
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/welcome/",
        {},
    )
    assert status == 401


# ---------------------------------------------------------------------
# Resume-target handler
# ---------------------------------------------------------------------

def test_resume_target_pre_welcome_returns_root(wizard_process):
    wp = wizard_process
    session = open_session(wp)
    status, _, body = _http.get_json(
        f"{wp.base_url}/api/wizard/resume-target/",
        headers=session_headers(session),
    )
    assert status == 200, body
    assert body["route"] == "/"
    assert body["topology"] is None


def test_resume_target_after_welcome_and_topology_returns_network(wizard_process):
    wp = wizard_process
    session = open_session(wp)
    headers = session_headers(session)
    # Welcome → topology — both required before /network is the next
    # incomplete step (the walker treats welcome_seen as required).
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/welcome/", {}, headers=headers,
    )
    assert status == 200
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/topology/",
        {"topology": "manager"},
        headers=headers,
    )
    assert status == 200
    status, _, body = _http.get_json(
        f"{wp.base_url}/api/wizard/resume-target/",
        headers=headers,
    )
    assert status == 200, body
    assert body["route"] == "/network"
    assert body["topology"] == "manager"


def test_resume_target_requires_session(wizard_process):
    wp = wizard_process
    status, _, _ = _http.get_json(f"{wp.base_url}/api/wizard/resume-target/")
    assert status == 401
