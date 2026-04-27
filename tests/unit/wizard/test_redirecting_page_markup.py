# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Markup / structure / a11y tests for ``redirecting.html``.

Mirrors the original ``test_redirecting_page.py`` (Spec 1 / B4 /
FR-W-FE5). JS-behaviour assertions live in
``test_redirecting_page_behaviour.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

import re

from ._redirecting_page_helpers import (
    REDIRECTING_JS_PATH, REDIRECTING_PATH,
)

# Module-scoped fixtures (redirecting_html, redirecting_js, common_js,
# redirecting_combined) are loaded via tests/unit/wizard/conftest.py.


def test_redirecting_html_exists():
    assert REDIRECTING_PATH.is_file(), f"Missing {REDIRECTING_PATH}"


def test_redirecting_html_has_spdx_header(redirecting_html):
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in redirecting_html
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in redirecting_html


def test_redirecting_js_has_spdx_header(redirecting_js):
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in redirecting_js
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in redirecting_js


def test_redirecting_html_under_size_limit():
    """FR-W-FE7 / AC-W-FE2: wizard frontend HTML files MUST stay <= 250 lines."""
    lines = REDIRECTING_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"redirecting.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7."
    )


def test_redirecting_js_under_size_limit():
    """FR-W-FE7 also applies to the per-page JS file (Phase F2)."""
    lines = REDIRECTING_JS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"redirecting.js has {len(lines)} lines — exceeds the 250-line "
        "ceiling."
    )


def test_redirecting_html_loads_vendored_petite_vue(redirecting_js):
    assert "/static/vendor/petite-vue.js" in redirecting_js


def test_redirecting_html_loads_vendored_bootstrap_css(redirecting_html):
    assert "/static/vendor/bootstrap.min.css" in redirecting_html


def test_redirecting_html_loads_vendored_bootstrap_bundle_js(redirecting_html):
    """bootstrap.bundle.min.js MUST come BEFORE the per-page module script."""
    assert "/static/vendor/bootstrap.bundle.min.js" in redirecting_html
    bs_idx = redirecting_html.find("/static/vendor/bootstrap.bundle.min.js")
    js_idx = redirecting_html.find("/static/js/redirecting.js")
    assert bs_idx >= 0 and js_idx >= 0
    assert bs_idx < js_idx, (
        "bootstrap.bundle.min.js MUST be referenced before redirecting.js."
    )


def test_redirecting_html_loads_extracted_page_script(redirecting_html):
    """redirecting.html MUST reference the extracted redirecting.js module."""
    assert "/static/js/redirecting.js" in redirecting_html
    assert 'type="module"' in redirecting_html


def test_redirecting_html_has_nomodule_fallback(redirecting_html):
    """NF-11 / browser-compat: <script nomodule> fallback required."""
    assert "<script nomodule>" in redirecting_html


def test_redirecting_html_log_path_rendered_as_plain_text(redirecting_html):
    """SEC-MED-6 / SEC-v2.3-LOW-1: log path MUST render as text, not as a link.

    The wizard does NOT serve log content — the user opens the file with
    their text editor. We assert the logPath is rendered inside <code>
    (or similar plain-text element) rather than an <a href="...">.
    """
    # logPath is bound inside <code> blocks; verify no <a href that
    # interpolates logPath exists.
    assert "{{ logPath" in redirecting_html
    # Make sure logPath is never used as an href value.
    assert ":href=\"logPath" not in redirecting_html
    assert ':href="logPath' not in redirecting_html
    assert ":href='logPath" not in redirecting_html
    # And that <code> wraps the path display (visual cue it's a path).
    assert "<code" in redirecting_html


def test_redirecting_html_has_aria_live_region(redirecting_html):
    """a11y: status updates MUST be in an aria-live region for screen readers.

    Phase F2 (MEDIUM-4) added a binding so the inner region escalates
    to assertive when status === 'failed'. The :aria-live binding (a
    Petite-vue attribute bind) MUST appear, and a polite literal MUST
    still exist on at least one banner.
    """
    assert ':aria-live="statusLiveRegionPoliteness"' in redirecting_html
    assert 'aria-live="polite"' in redirecting_html


def test_redirecting_html_spinner_is_aria_hidden(redirecting_html):
    """Spinner is decorative; the textual status is what gets announced."""
    # Bootstrap spinner pattern — verify aria-hidden on the visible
    # spinner element.
    assert 'aria-hidden="true"' in redirecting_html
    assert "spinner-border" in redirecting_html


def test_redirecting_html_no_cdn_references(redirecting_html):
    """NF-3 / FR-W-FE10: no external CDN references allowed."""
    stripped_lines = []
    for line in redirecting_html.splitlines():
        s = line.strip()
        if s.startswith("<!--") or s.startswith("//") or s.startswith("*"):
            continue
        s = re.sub(r"<!--.*?-->", "", s)
        stripped_lines.append(s)
    body = "\n".join(stripped_lines)
    cdn_hits = re.findall(r"https?://[^\s\"'<>)]+", body)
    assert not cdn_hits, (
        "Wizard HTML must not reference any external URL — found: "
        f"{cdn_hits}. All assets MUST come from /static/vendor/."
    )


def test_redirecting_html_v_cloak_used(redirecting_html):
    """Petite-vue scopes should use v-cloak so users never see {{ }} flash."""
    assert "v-cloak" in redirecting_html


def test_redirecting_html_renders_tls_warning_banner(redirecting_html):
    """HIGH-2 (Phase F2): the TLS warning MUST appear above the spinner.

    Without the banner the user lands on the runtime URL, sees the
    self-signed cert prompt, and has no context for what's happening.
    The warning text covers FR-W-FE5's mandate.
    """
    lower = redirecting_html.lower()
    assert "security warning" in lower, (
        "redirecting.html MUST surface the FR-W-FE5 TLS warning above "
        "the spinner so the user knows the cert prompt is expected."
    )
    # The banner becomes a clickable link to the runtime URL when ready.
    assert ':href="runtimeUrl"' in redirecting_html, (
        "When status === 'ready' the runtime URL MUST be rendered as a "
        "clickable fallback link inside the banner (HIGH-2)."
    )


def test_redirecting_html_no_token_in_url(redirecting_combined):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment."""
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
        "?session_token=",
    ]
    for pat in bad_patterns:
        assert pat not in redirecting_combined, (
            f"Token must not appear in URL — found pattern {pat!r}."
        )
