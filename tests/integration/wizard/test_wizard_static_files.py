# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard static-file serving + path-traversal protection tests.

Covers Spec 1 / D1 scenario 6: vendored assets render with the right
Content-Type, missing assets return 404, and crafted ``..`` traversal
attempts cannot escape the static root.
"""

from __future__ import annotations

from . import _http


def test_static_vendor_petite_vue_served(wizard_process):
    """The Petite-vue bundle is served as JavaScript."""
    url = f"{wizard_process.base_url}/static/vendor/petite-vue.js"
    status, headers, body = _http.get(url)
    assert status == 200, body[:200]
    ct = headers.get("Content-Type", "")
    assert ct.startswith("application/javascript"), ct
    # The bundle is non-trivial — sanity-check we got real JS, not a
    # 200 OK with an empty body.
    assert len(body) > 1000, len(body)
    # B1 vendored Petite-vue 0.4.1 — the file is plain ASCII source.
    assert b"function" in body or b"export" in body, body[:200]


def test_static_vendor_bootstrap_css_served(wizard_process):
    """Bootstrap's CSS bundle is served as text/css."""
    url = f"{wizard_process.base_url}/static/vendor/bootstrap.min.css"
    status, headers, body = _http.get(url)
    assert status == 200, body[:200]
    assert headers.get("Content-Type", "").startswith("text/css"), (
        headers.get("Content-Type")
    )
    assert len(body) > 1000, len(body)


def test_static_path_traversal_blocked(wizard_process):
    """``..`` segments cannot escape the static root.

    The handler resolves the candidate path and refuses to serve
    anything outside :data:`STATIC_ROOT`. Encoded and unencoded
    traversal attempts must both yield 404.
    """
    base = wizard_process.base_url
    traversal_urls = [
        f"{base}/static/vendor/../../etc/passwd",
        f"{base}/static/vendor/../../../wizard/run_wizard.py",
        f"{base}/static/css/../vendor/petite-vue.js",
    ]
    for url in traversal_urls:
        status, _, body = _http.get(url)
        assert status == 404, (url, status, body[:200])


def test_static_nonexistent_returns_404(wizard_process):
    """Unknown vendor / css filenames return 404 (not 500)."""
    base = wizard_process.base_url
    for url in (
        f"{base}/static/vendor/does-not-exist.js",
        f"{base}/static/css/missing.css",
    ):
        status, _, _ = _http.get(url)
        assert status == 404, (url, status)


def test_static_unknown_route_returns_404(wizard_process):
    """Routes outside the configured prefixes 404 cleanly."""
    base = wizard_process.base_url
    status, _, _ = _http.get(f"{base}/something/random/")
    assert status == 404, status


def test_topology_html_served_at_topology_route(wizard_process):
    """``GET /topology`` serves the topology picker page (FR-W-FE9)."""
    status, headers, body = _http.get(f"{wizard_process.base_url}/topology")
    assert status == 200, body[:200]
    assert headers.get("Content-Type", "").startswith("text/html"), headers
    text = body.decode("utf-8", errors="replace")
    assert "<title>Sethlans" in text, text[:400]
    # FR-W-FE2 security headers apply to HTML pages.
    assert headers.get("Content-Security-Policy"), headers


def test_redirecting_html_served(wizard_process):
    """``GET /redirecting`` serves the post-done waiting page (B4)."""
    status, headers, body = _http.get(
        f"{wizard_process.base_url}/redirecting",
    )
    assert status == 200, body[:200]
    assert headers.get("Content-Type", "").startswith("text/html"), headers


def test_csp_script_src_drops_unsafe_inline(wizard_process):
    """Issue #146 — `script-src` must NOT carry `'unsafe-inline'`.

    After Phase F2 lifted page scripts to ES modules and the
    nomodule legacy-browser fallback to ``static/js/legacy-fallback.js``,
    every wizard HTML page references its scripts via ``src=``.
    Keeping ``'unsafe-inline'`` in `script-src` would weaken CSP
    against XSS for no benefit.

    `style-src` deliberately keeps ``'unsafe-inline'`` because Bootstrap
    injects style attributes via JS (tooltips, modals, etc.); that
    constraint isn't part of this regression.
    """
    base = wizard_process.base_url
    for path in ("/", "/topology", "/redirecting"):
        _, headers, body = _http.get(f"{base}{path}")
        csp = headers.get("Content-Security-Policy", "")
        assert csp, (path, headers)
        # Parse the CSP into directive -> sources.
        directives: dict[str, list[str]] = {}
        for chunk in csp.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            name, _, rest = chunk.partition(" ")
            directives[name.strip().lower()] = rest.strip().split()
        script_src = directives.get("script-src", [])
        assert "'unsafe-inline'" not in script_src, (
            path, "script-src must not include 'unsafe-inline' "
            f"(found: {script_src!r})",
        )
        assert "'self'" in script_src, (path, script_src)
        # Issue #171 — Petite-vue compiles reactive expressions with
        # `new Function(...)`, which the browser blocks unless
        # `'unsafe-eval'` is present. Without it every v-model / :class /
        # @submit binding silently fails and the wizard is unusable
        # (token input renders the literal string "undefined", Continue
        # button never enables). Guard the regression here.
        assert "'unsafe-eval'" in script_src, (
            path, "script-src must include 'unsafe-eval' for Petite-vue "
            f"(found: {script_src!r})",
        )
        # Defense in depth — confirm no <script> block on the page is
        # missing a src= attribute (which would silently fail under
        # the tightened CSP and is the regression we're guarding).
        text = body.decode("utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("<script"):
                continue
            # Permit either inline-but-empty (rare) or a src=-bearing tag.
            assert "src=" in stripped, (
                path, line,
                "Inline <script> bodies are forbidden by the tightened CSP.",
            )


def test_legacy_fallback_js_served(wizard_process):
    """The extracted legacy-browser fallback is reachable as static JS."""
    url = f"{wizard_process.base_url}/static/js/legacy-fallback.js"
    status, headers, body = _http.get(url)
    assert status == 200, body[:200]
    ct = headers.get("Content-Type", "")
    assert ct.startswith("application/javascript"), ct
    # Sanity: the fallback's user-visible string is present.
    assert b"Unsupported browser" in body, body[:200]
