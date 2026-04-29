# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) CSP regression — no inline event handlers.

The wizard's CSP forbids inline JavaScript (`script-src 'self'`), so
inline event-handler attributes (``onclick``, ``onsubmit``,
``onload``, etc.) MUST NOT appear in any of the new HTML pages. Only
Petite-vue's ``@click`` / ``v-on:click`` directives are allowed —
they bind via JavaScript at mount time and never register an inline
HTML handler.

This is a static grep over the served HTML rather than a Playwright
DOM probe so the check covers the `text/html` payload exactly as the
browser sees it (no Petite-vue mount mutations).
"""

from __future__ import annotations

import re
import urllib.request

import pytest

# All pages introduced or modified in Phase 2.
PAGES = [
    "/",
    "/token",
    "/topology",
    "/network",
    "/database",
    "/admin-user",
    "/worker-password",
    "/ffmpeg",
    "/verify",
    "/done",
]

# Inline handler attribute names we forbid (subset that's actually
# observed in legacy templates; not exhaustive but covers the common
# regressions).
INLINE_HANDLER_PATTERN = re.compile(
    r'\s(?:onclick|onsubmit|onload|onerror|onchange|oninput|onkeydown|'
    r'onkeyup|onkeypress|onmouseover|onmouseout|onfocus|onblur)\s*=',
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", PAGES)
def test_no_inline_event_handlers(path, wizard_process):
    url = f"{wizard_process.base_url}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        html = resp.read().decode("utf-8")
    matches = INLINE_HANDLER_PATTERN.findall(html)
    assert not matches, (
        f"Page {path} contains inline event-handler attribute(s): "
        f"{matches}. CSP forbids these — replace with Petite-vue "
        f"@event directives bound at mount time."
    )


@pytest.mark.parametrize("path", PAGES)
def test_csp_header_blocks_inline_scripts(path, wizard_process):
    """The wizard's CSP must NOT carry 'unsafe-inline' for script-src
    on Phase 2 pages."""
    url = f"{wizard_process.base_url}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        csp = resp.headers.get("Content-Security-Policy", "")
    assert csp, f"No CSP header on {path}"
    # The directive granting inline-script execution would invalidate
    # the whole CSP-vs-XSS posture for the wizard.
    script_src = ""
    for directive in csp.split(";"):
        d = directive.strip()
        if d.startswith("script-src"):
            script_src = d
            break
    if script_src:
        assert "'unsafe-inline'" not in script_src, (path, script_src)
