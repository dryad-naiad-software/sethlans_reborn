# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""WSGI tests for the wizard's HTML page routes (Spec 1 / B2 / FR-W-FE2).

Covers the three HTML routes wired into the router by
``server.create_app``:

* ``GET /``                 → ``index.html``
* ``GET /topology``         → ``topology.html`` (FR-W-FE9)
* ``GET /redirecting``      → ``redirecting.html`` (FR-W-FE5)

And, on every HTML response, the FR-W-FE2 security header set
(Content-Security-Policy, X-Content-Type-Options, Referrer-Policy,
Permissions-Policy).

Vendor / CSS / JS routes live in ``test_static_file_routes_assets.py``;
direct unit tests of the static-file handler factory live in
``test_static_file_routes_handler.py``. Each file stays under the
300-line limit enforced by CLAUDE.md.
"""

from __future__ import annotations

from wizard.sethlans_wizard import server

from ._static_file_helpers import (
    IPC_SECRET, SETUP_TOKEN, get_environ, invoke, post_environ,
)

# The _reset_auth_state autouse fixture is provided by
# tests/unit/wizard/conftest.py.


# ---------------------------------------------------------------------
# / → index.html (FR-W-FE2)
# ---------------------------------------------------------------------

class TestIndexRoute:

    def test_get_root_returns_index_html(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(app, get_environ("/"))
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/html"), headers
        assert b"<!doctype html>" in body.lower() or b"<!DOCTYPE html>" in body

    def test_index_response_carries_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, headers, _ = invoke(app, get_environ("/"))
        # FR-W-FE2 mandates these headers on every HTML response.
        assert "Content-Security-Policy" in headers
        csp = headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_root_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, _ = invoke(app, post_environ("/"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")


# ---------------------------------------------------------------------
# /topology → topology.html (FR-W-FE9)
# ---------------------------------------------------------------------

class TestTopologyRoute:

    def test_get_topology_returns_topology_html(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(app, get_environ("/topology"))
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/html"), headers
        # Sanity: must be the topology page, NOT the index page.
        # The radiogroup role is unique to topology.html.
        assert b'role="radiogroup"' in body, (
            "GET /topology must return topology.html (radiogroup), "
            "not index.html."
        )
        # Distinguishing markers that live in topology.html (not JS):
        # the page heading + the topology.js module reference.
        assert b"Choose Topology" in body
        assert b"/static/js/topology.js" in body

    def test_topology_response_carries_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, headers, _ = invoke(app, get_environ("/topology"))
        # FR-W-FE2 mandates these headers on every HTML response.
        assert "Content-Security-Policy" in headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_topology_page_returns_405(self, tmp_path):
        # POST /topology hits the static page route (NOT the API
        # /api/wizard/topology/), so it MUST 405. The API route is
        # exact-equal matched ahead of this in the router.
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, _ = invoke(app, post_environ("/topology"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")

    def test_topology_route_distinct_from_index(self, tmp_path):
        """GET / must return index.html; GET /topology must return topology.html."""
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, _, root_body = invoke(app, get_environ("/"))
        _, _, topology_body = invoke(app, get_environ("/topology"))
        assert root_body != topology_body, (
            "Index and topology pages must serve different content."
        )
        # index.html has the setup-token form; topology.html has the
        # radiogroup. Confirm no swap.
        assert b"setup-token" in root_body
        assert b'role="radiogroup"' not in root_body
        assert b'role="radiogroup"' in topology_body


# ---------------------------------------------------------------------
# /redirecting → redirecting.html (FR-W-FE5)
# ---------------------------------------------------------------------

class TestRedirectingRoute:

    def test_get_redirecting_returns_redirecting_html(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(app, get_environ("/redirecting"))
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/html"), headers
        # Distinguishing markers that live in redirecting.html (not JS):
        # the page heading + the redirecting.js module reference.
        assert b"/static/js/redirecting.js" in body, (
            "GET /redirecting must return redirecting.html (loads "
            "redirecting.js), not index.html or topology.html."
        )
        assert b"Starting Sethlans" in body or b"Starting Sethlans&hellip;" in body

    def test_redirecting_response_carries_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, headers, _ = invoke(app, get_environ("/redirecting"))
        assert "Content-Security-Policy" in headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_redirecting_page_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, _ = invoke(app, post_environ("/redirecting"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")

    def test_redirecting_route_distinct_from_other_pages(self, tmp_path):
        """All three wizard pages must serve different content."""
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, _, root_body = invoke(app, get_environ("/"))
        _, _, topology_body = invoke(app, get_environ("/topology"))
        _, _, redirecting_body = invoke(app, get_environ("/redirecting"))
        assert root_body != redirecting_body
        assert topology_body != redirecting_body
        # Each page loads its own per-page module script (Phase F2).
        assert b"/static/js/redirecting.js" in redirecting_body
        assert b"/static/js/redirecting.js" not in root_body
        assert b"/static/js/redirecting.js" not in topology_body
        assert b"/static/js/auth.js" in root_body
        assert b"/static/js/topology.js" in topology_body
