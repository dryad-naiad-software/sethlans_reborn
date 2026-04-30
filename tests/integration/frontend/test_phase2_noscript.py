# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) <noscript> regression — every new page must render
the legacy-fallback message when JS is disabled (FR-VENDOR3 +
FR-FE-A11Y).

This test uses a JavaScript-disabled browser context so the
``<noscript>`` block becomes the visible content. Each page MUST
display its fallback message; the assertion uses Playwright's text
inspection to verify the user-facing copy reaches the screen.
"""

from __future__ import annotations

import pytest

PHASE2_PAGES = [
    "/",
    "/token",
    "/topology",
    "/network",
    "/database",
    "/admin-user",
    "/worker-password",
    "/verify",
    "/done",
]


@pytest.fixture
def context_no_js(browser):
    ctx = browser.new_context(java_script_enabled=False, ignore_https_errors=True)
    yield ctx
    ctx.close()


@pytest.mark.parametrize("path", PHASE2_PAGES)
def test_noscript_block_renders_when_js_disabled(
    context_no_js, wizard_process, path,
):
    page = context_no_js.new_page()
    page.goto(f"{wizard_process.base_url}{path}")
    body_text = page.locator("body").inner_text()
    # Each page's <noscript> message includes either "JavaScript" or the
    # word "enable JavaScript". The exact wording differs per page, but
    # the user-facing fallback always mentions JavaScript.
    assert "JavaScript" in body_text, (path, body_text)
