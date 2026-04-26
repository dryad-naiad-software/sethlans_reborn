# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""WSGI tests for the wizard's static-file routes (Spec 1 / B2 / FR-W-FE2).

Covers the three mounts wired into the router by ``server.create_app``:

* ``GET /``                          → ``index.html``
* ``GET /static/vendor/<path>``      → vendored Petite-vue + Bootstrap
* ``GET /static/css/<path>``         → page-specific CSS

And the security-critical surface:

* path traversal (``..`` segments, NUL bytes, escapes via the resolved
  prefix) MUST 404,
* method other than GET / HEAD MUST 405,
* HTML responses MUST carry the FR-W-FE2 security header set,
* unknown nested paths MUST 404.

The static handler is also exercised directly via
``handlers.static_files`` so the path-traversal logic is testable
without spinning up the full app.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from wizard.sethlans_wizard import auth_state, server
from wizard.sethlans_wizard.handlers.static_files import (
    make_static_handler,
)


_SETUP_TOKEN = b"setup-token-xyz-99887766554433221100"
_IPC_SECRET = b"ipc-hmac-secret-bytes-zzz-yyy-xxx"


@pytest.fixture(autouse=True)
def _reset_auth_state():
    auth_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()


def _invoke(app, environ):
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    return captured.get("status"), captured.get("headers", {}), body


def _get_environ(path: str) -> dict:
    return {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }


def _post_environ(path: str) -> dict:
    env = _get_environ(path)
    env["REQUEST_METHOD"] = "POST"
    return env


# ---------------------------------------------------------------------
# / → index.html (FR-W-FE2)
# ---------------------------------------------------------------------

class TestIndexRoute:

    def test_get_root_returns_index_html(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(app, _get_environ("/"))
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/html"), headers
        assert b"<!doctype html>" in body.lower() or b"<!DOCTYPE html>" in body

    def test_index_response_carries_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, headers, _ = _invoke(app, _get_environ("/"))
        # FR-W-FE2 mandates these headers on every HTML response.
        assert "Content-Security-Policy" in headers
        csp = headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_root_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, _ = _invoke(app, _post_environ("/"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")


# ---------------------------------------------------------------------
# /topology → topology.html (FR-W-FE9)
# ---------------------------------------------------------------------

class TestTopologyRoute:

    def test_get_topology_returns_topology_html(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(app, _get_environ("/topology"))
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
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, headers, _ = _invoke(app, _get_environ("/topology"))
        # FR-W-FE2 mandates these headers on every HTML response.
        assert "Content-Security-Policy" in headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_topology_page_returns_405(self, tmp_path):
        # POST /topology hits the static page route (NOT the API
        # /api/wizard/topology/), so it MUST 405. The API route is
        # exact-equal matched ahead of this in the router.
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, _ = _invoke(app, _post_environ("/topology"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")

    def test_topology_route_distinct_from_index(self, tmp_path):
        """GET / must return index.html; GET /topology must return topology.html."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, _, root_body = _invoke(app, _get_environ("/"))
        _, _, topology_body = _invoke(app, _get_environ("/topology"))
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
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(app, _get_environ("/redirecting"))
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
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, headers, _ = _invoke(app, _get_environ("/redirecting"))
        assert "Content-Security-Policy" in headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
        assert "interest-cohort=()" in headers.get("Permissions-Policy", "")

    def test_post_redirecting_page_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, _ = _invoke(app, _post_environ("/redirecting"))
        assert status.startswith("405"), status
        assert "GET" in headers.get("Allow", "")

    def test_redirecting_route_distinct_from_other_pages(self, tmp_path):
        """All three wizard pages must serve different content."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, _, root_body = _invoke(app, _get_environ("/"))
        _, _, topology_body = _invoke(app, _get_environ("/topology"))
        _, _, redirecting_body = _invoke(app, _get_environ("/redirecting"))
        assert root_body != redirecting_body
        assert topology_body != redirecting_body
        # Each page loads its own per-page module script (Phase F2).
        assert b"/static/js/redirecting.js" in redirecting_body
        assert b"/static/js/redirecting.js" not in root_body
        assert b"/static/js/redirecting.js" not in topology_body
        assert b"/static/js/auth.js" in root_body
        assert b"/static/js/topology.js" in topology_body


# ---------------------------------------------------------------------
# /static/vendor/* — vendored Petite-vue + Bootstrap
# ---------------------------------------------------------------------

class TestVendorRoute:

    def test_get_vendored_petite_vue(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(
            app, _get_environ("/static/vendor/petite-vue.js"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith(
            "application/javascript"
        ), headers
        # First few bytes of the vendored file should be JS — sanity
        # check that we're returning the file, not an error envelope.
        assert len(body) > 1024

    def test_get_vendored_bootstrap_css(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(
            app, _get_environ("/static/vendor/bootstrap.min.css"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/css"), headers
        assert len(body) > 1024

    def test_get_nonexistent_vendor_file_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/vendor/does-not-exist.js"),
        )
        assert status.startswith("404"), status

    def test_path_traversal_returns_404(self, tmp_path):
        """Critical security control: ``..`` MUST NOT escape the root."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        for path in (
            "/static/vendor/../../../etc/passwd",
            "/static/vendor/../server.py",
            "/static/vendor/..%2F..%2Fserver.py",
        ):
            status, _, _ = _invoke(app, _get_environ(path))
            assert status.startswith("404"), (
                f"Path traversal {path!r} must 404, got {status!r}"
            )

    def test_nul_byte_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/vendor/petite-vue.js\x00.txt"),
        )
        assert status.startswith("404"), status

    def test_post_vendor_returns_405(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _post_environ("/static/vendor/petite-vue.js"),
        )
        assert status.startswith("405"), status

    def test_vendor_response_has_baseline_security_headers(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        _, headers, _ = _invoke(
            app, _get_environ("/static/vendor/petite-vue.js"),
        )
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"


# ---------------------------------------------------------------------
# /static/css/* — page-specific overrides
# ---------------------------------------------------------------------

class TestCssRoute:

    def test_get_css_file(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(
            app, _get_environ("/static/css/wizard.css"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith("text/css"), headers
        assert b"v-cloak" in body or len(body) > 0

    def test_get_nonexistent_css_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/css/missing.css"),
        )
        assert status.startswith("404"), status


# ---------------------------------------------------------------------
# /static/js/* — per-page extracted module scripts (Phase F2)
# ---------------------------------------------------------------------

class TestJsRoute:

    def test_get_common_js(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, body = _invoke(
            app, _get_environ("/static/js/common.js"),
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
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, headers, _ = _invoke(
            app, _get_environ("/static/js/auth.js"),
        )
        assert status.startswith("200"), status
        assert headers.get("Content-Type", "").startswith(
            "application/javascript"
        ), headers

    def test_get_topology_js(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/js/topology.js"),
        )
        assert status.startswith("200"), status

    def test_get_redirecting_js(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/js/redirecting.js"),
        )
        assert status.startswith("200"), status

    def test_get_nonexistent_js_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        status, _, _ = _invoke(
            app, _get_environ("/static/js/missing.js"),
        )
        assert status.startswith("404"), status

    def test_js_route_path_traversal_blocked(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        for path in (
            "/static/js/../server.py",
            "/static/js/../../etc/passwd",
        ):
            status, _, _ = _invoke(app, _get_environ(path))
            assert status.startswith("404"), (
                f"Path traversal {path!r} must 404, got {status!r}"
            )


# ---------------------------------------------------------------------
# Direct unit tests of the static-file factory (so the path-traversal
# logic can be exercised against a synthetic root, not only the real
# vendored bundle).
# ---------------------------------------------------------------------

class TestStaticHandlerDirectly:

    def _make(self, root: Path, prefix: str = "/x/"):
        return make_static_handler(root, prefix)

    def test_make_static_handler_rejects_prefix_without_trailing_slash(
        self, tmp_path,
    ):
        with pytest.raises(ValueError):
            make_static_handler(tmp_path, "/x")

    def test_serves_known_extension(self, tmp_path):
        (tmp_path / "a.css").write_text("body{}", encoding="utf-8")
        handler = self._make(tmp_path)
        status, headers, body = _invoke(handler, _get_environ("/x/a.css"))
        assert status.startswith("200"), status
        assert headers["Content-Type"].startswith("text/css")
        assert body == b"body{}"

    def test_unknown_extension_404s(self, tmp_path):
        # Even if the file exists, an unknown extension (e.g., .py) must
        # not be served — defense in depth against a stray file.
        (tmp_path / "secret.py").write_text("print(1)", encoding="utf-8")
        handler = self._make(tmp_path)
        status, _, _ = _invoke(handler, _get_environ("/x/secret.py"))
        assert status.startswith("404"), status

    def test_empty_path_404s(self, tmp_path):
        handler = self._make(tmp_path)
        status, _, _ = _invoke(handler, _get_environ("/x/"))
        assert status.startswith("404"), status

    def test_path_outside_prefix_404s(self, tmp_path):
        handler = self._make(tmp_path)
        status, _, _ = _invoke(handler, _get_environ("/y/whatever.css"))
        assert status.startswith("404"), status

    def test_head_returns_no_body(self, tmp_path):
        (tmp_path / "a.css").write_text("body{}", encoding="utf-8")
        handler = self._make(tmp_path)
        env = _get_environ("/x/a.css")
        env["REQUEST_METHOD"] = "HEAD"
        status, headers, body = _invoke(handler, env)
        assert status.startswith("200")
        assert headers["Content-Length"] == "6"
        assert body == b""

    def test_traversal_via_resolve_blocked(self, tmp_path):
        # Create a file outside the allowed root, then try to escape to
        # it via "..".
        outside = tmp_path.parent / "secret-outside.css"
        outside.write_text("nope", encoding="utf-8")
        try:
            handler = self._make(tmp_path)
            status, _, _ = _invoke(
                handler, _get_environ("/x/../secret-outside.css"),
            )
            assert status.startswith("404"), status
        finally:
            outside.unlink(missing_ok=True)
