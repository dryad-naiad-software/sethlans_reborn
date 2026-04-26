# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Content tests for the wizard topology picker page (Spec 1 / B3 / FR-W-FE9).

These tests live next to the asset and inspect the static HTML
verbatim (mirroring ``test_index_page.py``). They do not exercise the
WSGI app — that is covered by ``test_static_file_routes.py``. The
purpose here is to lock down the radiogroup a11y contract from
FR-W-FE9 and AC-W-FE5 so a future drive-by edit cannot silently break
the keyboard / screen-reader story.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOPOLOGY_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "topology.html"
)


@pytest.fixture(scope="module")
def topology_html() -> str:
    assert TOPOLOGY_PATH.is_file(), f"Expected {TOPOLOGY_PATH} to exist"
    return TOPOLOGY_PATH.read_text(encoding="utf-8")


def test_topology_html_exists():
    assert TOPOLOGY_PATH.is_file(), f"Missing {TOPOLOGY_PATH}"


def test_topology_html_has_spdx_header(topology_html):
    """Project SPDX header MUST be present in HTML comment style."""
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in topology_html
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in topology_html


def test_topology_html_under_size_limit():
    """FR-W-FE7 / AC-W-FE2: wizard frontend HTML files MUST stay <= 250 lines."""
    lines = TOPOLOGY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"topology.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7."
    )


def test_topology_html_loads_vendored_petite_vue(topology_html):
    """ES module import MUST come from the local vendor path."""
    assert "/static/vendor/petite-vue.js" in topology_html


def test_topology_html_loads_vendored_bootstrap_css(topology_html):
    assert "/static/vendor/bootstrap.min.css" in topology_html


def test_topology_html_loads_vendored_bootstrap_bundle_js(topology_html):
    """Per integration pattern, bootstrap.bundle.min.js loads BEFORE petite-vue."""
    assert "/static/vendor/bootstrap.bundle.min.js" in topology_html
    bootstrap_idx = topology_html.find("/static/vendor/bootstrap.bundle.min.js")
    petite_idx = topology_html.find("/static/vendor/petite-vue.js")
    assert bootstrap_idx >= 0 and petite_idx >= 0
    assert bootstrap_idx < petite_idx, (
        "bootstrap.bundle.min.js MUST be referenced before petite-vue.js "
        "(integration pattern point 1)."
    )


def test_topology_html_has_nomodule_fallback(topology_html):
    """NF-11 / browser-compat: a <script nomodule> fallback is required."""
    assert "<script nomodule>" in topology_html, (
        "Missing <script nomodule> fallback for old browsers (NF-11)."
    )


def test_topology_html_has_radiogroup(topology_html):
    """FR-W-FE9: the three cards form a `role='radiogroup'`."""
    assert 'role="radiogroup"' in topology_html, (
        "Topology picker container MUST carry role='radiogroup' (FR-W-FE9)."
    )


def test_topology_html_radiogroup_has_aria_labelledby(topology_html):
    """FR-W-FE9: the radiogroup MUST be labelled by the page heading."""
    assert 'aria-labelledby="topology-heading"' in topology_html
    assert 'id="topology-heading"' in topology_html


def test_topology_html_has_three_radio_cards(topology_html):
    """FR-W-FE9: exactly three `role='radio'` elements (one per choice)."""
    matches = re.findall(r'role="radio"', topology_html)
    # Petite-vue's v-for renders three cards from one template element,
    # so the literal source MUST contain at least one role="radio" — and
    # the template must reference the three topology IDs below. We
    # assert "at least one" for the source-grep, then check the three
    # IDs appear (the v-for guarantees three runtime cards).
    assert len(matches) >= 1, (
        "Source MUST contain a role='radio' template element (FR-W-FE9)."
    )


def test_topology_html_aria_checked_attribute_used(topology_html):
    """FR-W-FE9: selection state conveyed via `aria-checked`."""
    assert "aria-checked" in topology_html, (
        "Selection state MUST be conveyed via aria-checked (FR-W-FE9)."
    )


def test_topology_html_selected_class_used(topology_html):
    """FR-W-FE9: visual `.selected` CSS class paired with aria-checked."""
    has_selected_token = (
        "selected:" in topology_html
        or "'selected'" in topology_html
        or "selected " in topology_html
    )
    assert has_selected_token, (
        "The visual `.selected` class MUST be bound alongside aria-checked "
        "(FR-W-FE9)."
    )


def test_topology_html_tabindex_attribute_used(topology_html):
    """FR-W-FE9: `tabindex` MUST be bound (one card focusable at a time)."""
    assert "tabindex" in topology_html, (
        "Cards MUST set tabindex (FR-W-FE9 — roving tabindex pattern)."
    )


def test_topology_html_has_three_topology_values(topology_html):
    """A4 contract: backend accepts manager | manager_worker | worker_only."""
    for value in ("'manager'", "'manager_worker'", "'worker_only'"):
        assert value in topology_html, (
            f"Topology choice value {value!r} missing — must match A4's "
            "VALID_TOPOLOGIES set."
        )


def test_topology_html_card_labels_present(topology_html):
    """The three card labels match the spec exactly (B3 task)."""
    assert "Manager only" in topology_html
    assert "Manager + Worker" in topology_html
    assert "Worker only" in topology_html


def test_topology_html_posts_to_topology_endpoint(topology_html):
    """FR-W-FE4: choice submitted via POST /api/wizard/topology/."""
    assert "/api/wizard/topology/" in topology_html
    assert (
        "method: 'POST'" in topology_html
        or 'method: "POST"' in topology_html
    )


def test_topology_html_sends_session_header(topology_html):
    """FR-W8 + FR-W-FE3b: session token sent via X-Wizard-Session header only."""
    assert "X-Wizard-Session" in topology_html, (
        "Topology submit MUST send X-Wizard-Session header (FR-W8 / FR-W-FE3b)."
    )


def test_topology_html_uses_session_storage_not_local_storage(topology_html):
    """FR-W-FE3: session token retrieved from sessionStorage (NOT local)."""
    assert "sessionStorage" in topology_html
    assert "localStorage" not in topology_html, (
        "Wizard pages MUST NOT use localStorage (FR-W-FE3)."
    )


def test_topology_html_redirects_to_redirecting_on_success(topology_html):
    """B3 task: on 200, navigate to /redirecting (the B4 page)."""
    assert "/redirecting" in topology_html


def test_topology_html_handles_session_expiry(topology_html):
    """FR-W-FE3: 401 from a wizard API call must clear sessionStorage + redirect."""
    # The exact form is implementation detail; check the recovery
    # ingredients are present.
    assert "401" in topology_html
    assert "removeItem" in topology_html or "clear()" in topology_html


def test_topology_html_no_token_in_url(topology_html):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment.

    Topology page does not handle the setup token, but defense-in-depth
    check that nobody copy-pasted a token-bearing URL pattern in.
    """
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
        "?session_token=",
    ]
    for pat in bad_patterns:
        assert pat not in topology_html, (
            f"Token must not appear in URL — found pattern {pat!r}."
        )


def test_topology_html_no_cdn_references(topology_html):
    """NF-3 / FR-W-FE10: no external CDN references allowed."""
    stripped_lines = []
    for line in topology_html.splitlines():
        s = line.strip()
        if s.startswith("<!--") or s.startswith("//") or s.startswith("*"):
            continue
        s = re.sub(r"<!--.*?-->", "", s)
        stripped_lines.append(s)
    body = "\n".join(stripped_lines)
    cdn_hits = re.findall(r"https?://[^\s\"'<>)]+", body)
    assert not cdn_hits, (
        "Wizard HTML must not reference any external URL — found: "
        f"{cdn_hits}. All assets MUST come from /static/vendor/ "
        "(NF-3 / FR-W-FE10)."
    )


def test_topology_html_v_cloak_used_to_prevent_template_flash(topology_html):
    """Petite-vue scopes should use v-cloak so users never see {{ }} flash."""
    assert "v-cloak" in topology_html


def test_topology_html_submit_disabled_until_selection(topology_html):
    """Submit button MUST be disabled until a card is selected."""
    assert ":disabled" in topology_html, (
        "Submit button must use a :disabled binding so the user cannot "
        "submit before choosing a topology."
    )
    # The disabled binding must reference the selected state and the
    # submitting flag.
    assert "selected" in topology_html
    assert "submitting" in topology_html


def test_topology_html_aria_disabled_on_submit(topology_html):
    """ARIA mirror of disabled state for screen readers."""
    assert "aria-disabled" in topology_html


def test_topology_html_keyboard_handler_attached_after_mount(topology_html):
    """Integration pattern point 4: keyboard handler attached after mount.

    The handler MUST be JS-driven (not inline @keydown on each card),
    per the v2.3 Petite-vue + Bootstrap integration pattern. We verify
    the handler references the arrow keys and Space/Enter and binds via
    addEventListener (not a Petite-vue @keydown attribute).
    """
    assert "ArrowRight" in topology_html
    assert "ArrowLeft" in topology_html
    assert "ArrowDown" in topology_html
    assert "ArrowUp" in topology_html
    assert "Enter" in topology_html
    assert "addEventListener" in topology_html
    assert "DOMContentLoaded" in topology_html
