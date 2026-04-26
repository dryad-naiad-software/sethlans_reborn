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
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INDEX_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "index.html"
)


@pytest.fixture(scope="module")
def index_html() -> str:
    assert INDEX_PATH.is_file(), f"Expected {INDEX_PATH} to exist"
    return INDEX_PATH.read_text(encoding="utf-8")


def test_index_html_exists():
    assert INDEX_PATH.is_file(), f"Missing {INDEX_PATH}"


def test_index_html_has_spdx_header(index_html):
    """Project SPDX header MUST be present in HTML comment style."""
    assert "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC" in index_html
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in index_html


def test_index_html_under_size_limit():
    """FR-W-FE7: every wizard frontend HTML file MUST stay under 250 lines."""
    lines = INDEX_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"index.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7. Split inline scripts into per-page JS "
        "files in static/js/ if you need more room."
    )


def test_index_html_loads_vendored_petite_vue(index_html):
    """ES module import MUST come from the local vendor path."""
    assert "/static/vendor/petite-vue.js" in index_html, (
        "Petite-vue must be loaded from /static/vendor/petite-vue.js"
    )


def test_index_html_loads_vendored_bootstrap_css(index_html):
    assert "/static/vendor/bootstrap.min.css" in index_html


def test_index_html_has_nomodule_fallback(index_html):
    """NF-11 / browser-compat: a <script nomodule> fallback is required."""
    assert "<script nomodule>" in index_html, (
        "Missing <script nomodule> fallback for old browsers (NF-11)."
    )


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


def test_index_html_posts_to_auth_endpoint(index_html):
    """FR-W-FE3a: token submission MUST go via POST to /api/wizard/auth/."""
    assert "/api/wizard/auth/" in index_html
    assert "method: 'POST'" in index_html or 'method: "POST"' in index_html


def test_index_html_uses_session_storage_not_local_storage(index_html):
    """FR-W-FE3: returned session_token stored in sessionStorage (NOT local)."""
    assert "sessionStorage" in index_html
    assert "localStorage" not in index_html, (
        "Wizard pages MUST NOT use localStorage; only sessionStorage "
        "for the session_token (FR-W-FE3)."
    )


def test_index_html_redirects_to_topology_on_success(index_html):
    """FR-W-FE3: on success, navigate to /topology."""
    assert "/topology" in index_html


def test_index_html_no_token_in_url(index_html):
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
        assert pat not in index_html, (
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
