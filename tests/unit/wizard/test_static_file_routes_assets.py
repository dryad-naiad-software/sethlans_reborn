# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""WSGI tests for the wizard's vendor / CSS / JS static-asset routes.

Covers the three asset mounts wired into the router by
``server.create_app``:

* ``GET /static/vendor/<path>``  → vendored Petite-vue + Bootstrap
* ``GET /static/css/<path>``     → page-specific CSS
* ``GET /static/js/<path>``      → per-page extracted module scripts
                                   (Phase F2)

Plus path-traversal / NUL-byte / 405 / unknown-file 404 guards on
each.

HTML page routes live in ``test_static_file_routes_pages.py``;
direct unit tests of the static-file handler factory live in
``test_static_file_routes_handler.py``.
"""

from __future__ import annotations

from wizard.sethlans_wizard import server

from ._static_file_helpers import (
    IPC_SECRET, SETUP_TOKEN, get_environ, invoke, post_environ,
)

# The _reset_auth_state autouse fixture is provided by
# tests/unit/wizard/conftest.py.


# ---------------------------------------------------------------------
# /static/vendor/* — vendored Petite-vue + Bootstrap
# ---------------------------------------------------------------------

class TestVendorRoute:

    def test_get_vendored_petite_vue(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(
            app, get_environ("/static/vendor/petite-vue.js"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith(
            "application/javascript"
        ), headers
        # First few bytes of the vendored file should be JS — sanity
        # check that we're returning the file, not an error envelope.
        assert len(body) > 1024

    def test_get_vendored_bootstrap_css(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(
            app, get_environ("/static/vendor/bootstrap.min.css"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/css"), headers
        assert len(body) > 1024

    def test_get_nonexistent_vendor_file_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/vendor/does-not-exist.js"),
        )
        assert status.startswith("404"), status

    def test_path_traversal_returns_404(self, tmp_path):
        """Critical security control: ``..`` MUST NOT escape the root."""
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        for path in (
            "/static/vendor/../../../etc/passwd",
            "/static/vendor/../server.py",
            "/static/vendor/..%2F..%2Fserver.py",
        ):
            status, _, _ = invoke(app, get_environ(path))
            assert status.startswith("404"), (
                f"Path traversal {path!r} must 404, got {status!r}"
            )

    def test_nul_byte_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/vendor/petite-vue.js\x00.txt"),
        )
        assert status.startswith("404"), status

    def test_post_vendor_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, post_environ("/static/vendor/petite-vue.js"),
        )
        assert status.startswith("405"), status

    def test_vendor_response_has_baseline_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        _, headers, _ = invoke(
            app, get_environ("/static/vendor/petite-vue.js"),
        )
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"


# ---------------------------------------------------------------------
# /static/css/* — page-specific overrides
# ---------------------------------------------------------------------

class TestCssRoute:

    def test_get_css_file(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(
            app, get_environ("/static/css/wizard.css"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/css"), headers
        assert b"v-cloak" in body or len(body) > 0

    def test_get_nonexistent_css_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/css/missing.css"),
        )
        assert status.startswith("404"), status


# ---------------------------------------------------------------------
# /static/js/* — per-page extracted module scripts (Phase F2)
# ---------------------------------------------------------------------

class TestJsRoute:

    def test_get_common_js(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, body = invoke(
            app, get_environ("/static/js/common.js"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith(
            "application/javascript"
        ), headers
        assert b"export" in body, (
            "common.js MUST be served as an ES module (export keyword "
            "should appear)."
        )

    def test_get_auth_js(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, headers, _ = invoke(
            app, get_environ("/static/js/auth.js"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith(
            "application/javascript"
        ), headers

    def test_get_topology_js(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/js/topology.js"),
        )
        assert status.startswith("200"), status

    def test_get_redirecting_js(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/js/redirecting.js"),
        )
        assert status.startswith("200"), status

    def test_get_nonexistent_js_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        status, _, _ = invoke(
            app, get_environ("/static/js/missing.js"),
        )
        assert status.startswith("404"), status

    def test_js_route_path_traversal_blocked(self, tmp_path):
        app = server.create_app(tmp_path, SETUP_TOKEN, IPC_SECRET)
        for path in (
            "/static/js/../server.py",
            "/static/js/../../etc/passwd",
        ):
            status, _, _ = invoke(app, get_environ(path))
            assert status.startswith("404"), (
                f"Path traversal {path!r} must 404, got {status!r}"
            )
