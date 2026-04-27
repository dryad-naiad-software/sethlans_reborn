# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Content tests for the wizard token-entry page (Spec 1 / B2 / FR-W-FE3).

These tests live next to the asset and inspect the static HTML
verbatim. They do not exercise the WSGI app — that is covered by
``test_static_file_routes.py``. The purpose here is to lock down the
attributes and structural elements the spec calls out by name, so a
future drive-by edit cannot silently break the security or
accessibility contract.

Phase F2 split the inline page script out into ``static/js/auth.js``
plus a shared ``static/js/common.js``. Markup-shape tests still inspect
``index.html`` directly; behaviour tests now read the JS files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INDEX_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "index.html"
)
AUTH_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "auth.js"
)
COMMON_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "common.js"
)


@pytest.fixture(scope="module")
def index_html() -> str:
    assert INDEX_PATH.is_file(), f"Expected {INDEX_PATH} to exist"
    return INDEX_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def auth_js() -> str:
    assert AUTH_JS_PATH.is_file(), f"Expected {AUTH_JS_PATH} to exist"
    return AUTH_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def common_js() -> str:
    assert COMMON_JS_PATH.is_file(), f"Expected {COMMON_JS_PATH} to exist"
    return COMMON_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_combined(index_html: str, auth_js: str, common_js: str) -> str:
    """Concatenated index.html + its JS deps for cross-file behavior tests."""
    return "\n".join((index_html, auth_js, common_js))


def test_index_html_exists():
    assert INDEX_PATH.is_file(), f"Missing {INDEX_PATH}"


def test_index_html_has_spdx_header(index_html):
    """Project SPDX header MUST be present in HTML comment style."""
    assert "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC" in index_html
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in index_html


def test_auth_js_has_spdx_header(auth_js):
    """Per CLAUDE.md NF-2 — the extracted page script also needs SPDX."""
    assert "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC" in auth_js
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in auth_js


def test_common_js_has_spdx_header(common_js):
    """Per CLAUDE.md NF-2 — the shared helpers module needs SPDX."""
    assert "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC" in common_js
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in common_js


def test_index_html_under_size_limit():
    """FR-W-FE7: every wizard frontend HTML file MUST stay under 250 lines."""
    lines = INDEX_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"index.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7. Split inline scripts into per-page JS "
        "files in static/js/ if you need more room."
    )


def test_auth_js_under_size_limit():
    """FR-W-FE7 also applies to the per-page JS files (Phase F2)."""
    lines = AUTH_JS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"auth.js has {len(lines)} lines — exceeds the 250-line ceiling."
    )


def test_common_js_under_size_limit():
    """FR-W-FE7 also applies to the shared JS module (Phase F2)."""
    lines = COMMON_JS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"common.js has {len(lines)} lines — exceeds the 250-line ceiling."
    )


def test_index_html_loads_vendored_petite_vue(auth_js):
    """ES module import MUST come from the local vendor path."""
    assert "/static/vendor/petite-vue.js" in auth_js, (
        "Petite-vue must be loaded from /static/vendor/petite-vue.js"
    )


def test_index_html_loads_vendored_bootstrap_css(index_html):
    assert "/static/vendor/bootstrap.min.css" in index_html


def test_index_html_loads_extracted_auth_script(index_html):
    """index.html MUST reference the extracted auth.js module (Phase F2)."""
    assert "/static/js/auth.js" in index_html
    assert 'type="module"' in index_html


def test_index_html_has_nomodule_fallback(index_html):
    """NF-11 / browser-compat: a <script nomodule> fallback is required.

    Issue #146 — the fallback is loaded from
    ``/static/js/legacy-fallback.js`` rather than an inline
    ``<script nomodule>`` block, so the CSP can drop
    ``script-src 'unsafe-inline'``.
    """
    assert (
        '<script nomodule src="/static/js/legacy-fallback.js"></script>'
        in index_html
    ), "Missing <script nomodule src=...> fallback for old browsers (NF-11)."


def test_index_html_token_input_attributes(index_html):
    """FR-W-FE3: the token input MUST carry the spec'd attribute set."""
    # The attributes are scattered across multiple lines; check each
    # separately rather than a single brittle regex.
    required_attrs = [
        'type="text"',
        'name="setup_token"',
        'autocomplete="one-time-code"',
        'inputmode="text"',
        'spellcheck="false"',
        'autocapitalize="off"',
        'aria-describedby="setup-token-help"',
    ]
    for attr in required_attrs:
        assert attr in index_html, (
            f"Token input is missing required attribute: {attr!r}"
        )


def test_index_html_helper_paragraph_id_matches_aria(index_html):
    """FE-v2.2-LOW-1: helper-text element id MUST match aria-describedby."""
    assert 'id="setup-token-help"' in index_html, (
        "Helper-text element must have id='setup-token-help' to match "
        "the input's aria-describedby attribute (FE-v2.2-LOW-1)."
    )


def test_index_html_helper_text_warns_against_chat_email(index_html):
    """v2.1-FE-LOW-1: helper text mirrors the FR-L3 banner guidance."""
    # Tolerant phrasing match — exact wording can rephrase but the
    # security warning MUST stay.
    lower = index_html.lower()
    assert "chat" in lower and "email" in lower, (
        "Helper text MUST warn against transmitting the token via chat "
        "or email (v2.1-FE-LOW-1 / FR-W-FE3)."
    )


def test_index_html_posts_to_auth_endpoint(auth_js):
    """FR-W-FE3a: token submission MUST go via POST to /api/wizard/auth/."""
    assert "/api/wizard/auth/" in auth_js
    assert "method: 'POST'" in auth_js or 'method: "POST"' in auth_js


def test_index_html_uses_session_storage_not_local_storage(common_js, auth_js):
    """FR-W-FE3: returned session_token stored in sessionStorage (NOT local).

    sessionStorage now lives in the shared common.js helpers module;
    auth.js calls into them via setSessionToken().
    """
    assert "sessionStorage" in common_js
    assert "setSessionToken" in auth_js
    assert "localStorage" not in common_js, (
        "Wizard pages MUST NOT use localStorage; only sessionStorage "
        "for the session_token (FR-W-FE3)."
    )
    assert "localStorage" not in auth_js


def test_index_html_redirects_to_topology_on_success(auth_js):
    """FR-W-FE3: on success, navigate to /topology."""
    assert "/topology" in auth_js


def test_index_html_no_token_in_url(index_combined):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment."""
    # The submit logic MUST NOT build a URL containing the token. A
    # simple check: there is no `?setup_token=` or `?token=` literal in
    # the file (the spec also forbids `#token=`).
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
    ]
    for pat in bad_patterns:
        assert pat not in index_combined, (
            f"Token must not appear in URL — found pattern {pat!r} "
            "(FR-W-FE3a)."
        )


def test_index_html_no_cdn_references(index_html):
    """NF-3 / FR-W-FE10: no external CDN references allowed.

    Strips comment lines first so the SPDX URL (if any) is excluded.
    Then asserts that no remaining ``http://`` or ``https://`` references
    survive — every asset MUST come from ``/static/vendor/`` or
    ``/static/css/``.
    """
    stripped_lines = []
    for line in index_html.splitlines():
        s = line.strip()
        if s.startswith("<!--") or s.startswith("//") or s.startswith("*"):
            continue
        # Strip inline HTML comments too (rough but adequate for our
        # tiny page — we don't have nested or multi-line comments
        # carrying URLs).
        s = re.sub(r"<!--.*?-->", "", s)
        stripped_lines.append(s)
    body = "\n".join(stripped_lines)
    cdn_hits = re.findall(r"https?://[^\s\"'<>)]+", body)
    assert not cdn_hits, (
        "Wizard HTML must not reference any external URL — found: "
        f"{cdn_hits}. All assets MUST come from /static/vendor/ "
        "(NF-3 / FR-W-FE10)."
    )


def test_index_html_v_cloak_used_to_prevent_template_flash(index_html):
    """Petite-vue scopes should use v-cloak so users never see {{ }} flash."""
    assert "v-cloak" in index_html, (
        "Add v-cloak to the reactive root (and a CSS rule hiding it) "
        "so the user never sees mustache syntax during page load."
    )


def test_index_html_disables_submit_while_in_flight(index_html):
    """Defensive: submit button MUST be disabled while the request flies.

    We look for a :disabled binding referencing the submitting flag —
    the exact form is implementation detail, but submitting MUST gate
    the button.
    """
    assert ":disabled" in index_html, (
        "Submit button must use a :disabled binding so the user can't "
        "double-submit while the auth request is in flight."
    )
    assert "submitting" in index_html


def test_auth_js_consumes_flash_message(auth_js):
    """HIGH-3 (Phase F2): the page MUST consume wizard:flashMessage on mount.

    topology.html writes a friendly "Your session expired" flash to
    sessionStorage when bouncing the user back to /. Without the read
    on this page the user gets no explanation of why they were
    redirected.
    """
    assert "consumeFlash" in auth_js, (
        "auth.js MUST call consumeFlash() so a session-expired flash "
        "from a downstream page is surfaced as the form error."
    )


def test_auth_js_uses_history_replace_state(auth_js):
    """MEDIUM-2 (Phase F2): replace history so Back skips token entry."""
    assert "history.replaceState" in auth_js, (
        "auth.js MUST call window.history.replaceState before the "
        "navigation so the browser Back button does not return the "
        "user to the token-entry page (MEDIUM-2)."
    )


def test_auth_js_uses_wall_clock_lockout(auth_js):
    """MEDIUM-5 (Phase F2): lockout timer MUST be wall-clock based.

    setInterval is throttled in background tabs, so the original
    "decrement on every tick" logic could over-report the remaining
    wait. The fix records an absolute deadline (Date.now() + N*1000).
    """
    assert "_lockoutDeadline" in auth_js
    assert "Date.now()" in auth_js
