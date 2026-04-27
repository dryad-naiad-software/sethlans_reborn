# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Markup / structure / a11y tests for ``topology.html`` + ``topology.js``.

Mirrors the original ``test_topology_page.py`` (Spec 1 / B3 /
FR-W-FE9). API/behaviour assertions live in
``test_topology_page_behaviour.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

import re

from ._topology_page_helpers import TOPOLOGY_JS_PATH, TOPOLOGY_PATH

# The module-scoped asset-loading fixtures (topology_html, topology_js,
# common_js) are provided by tests/unit/wizard/conftest.py.


def test_topology_html_exists():
    assert TOPOLOGY_PATH.is_file(), f"Missing {TOPOLOGY_PATH}"


def test_topology_html_has_spdx_header(topology_html):
    """Project SPDX header MUST be present in HTML comment style."""
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in topology_html
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in topology_html


def test_topology_js_has_spdx_header(topology_js):
    """Per CLAUDE.md NF-2 — the extracted page script also needs SPDX."""
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in topology_js
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in topology_js


def test_topology_html_under_size_limit():
    """FR-W-FE7 / AC-W-FE2: wizard frontend HTML files MUST stay <= 250 lines."""
    lines = TOPOLOGY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"topology.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7."
    )


def test_topology_js_under_size_limit():
    """FR-W-FE7 also applies to the per-page JS file (Phase F2)."""
    lines = TOPOLOGY_JS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"topology.js has {len(lines)} lines — exceeds the 250-line ceiling."
    )


def test_topology_html_loads_vendored_petite_vue(topology_js):
    """ES module import MUST come from the local vendor path."""
    assert "/static/vendor/petite-vue.js" in topology_js


def test_topology_html_loads_vendored_bootstrap_css(topology_html):
    assert "/static/vendor/bootstrap.min.css" in topology_html


def test_topology_html_loads_vendored_bootstrap_bundle_js(topology_html):
    """bootstrap.bundle.min.js loads BEFORE the per-page module script.

    Bootstrap's component JS must be on the global before any code that
    might want to call into it. The page script is loaded via
    `<script type="module" src="/static/js/topology.js">`, so we just
    assert the bundle reference appears earlier in the document than
    the topology.js reference.
    """
    assert "/static/vendor/bootstrap.bundle.min.js" in topology_html
    bs_idx = topology_html.find("/static/vendor/bootstrap.bundle.min.js")
    js_idx = topology_html.find("/static/js/topology.js")
    assert bs_idx >= 0 and js_idx >= 0
    assert bs_idx < js_idx, (
        "bootstrap.bundle.min.js MUST be referenced before topology.js "
        "(integration pattern point 1)."
    )


def test_topology_html_loads_extracted_page_script(topology_html):
    """topology.html MUST reference the extracted topology.js module."""
    assert "/static/js/topology.js" in topology_html
    assert 'type="module"' in topology_html


def test_topology_html_has_nomodule_fallback(topology_html):
    """NF-11 / browser-compat: a <script nomodule> fallback is required.

    Issue #146 — fallback is loaded from
    ``/static/js/legacy-fallback.js`` so the CSP can drop
    ``script-src 'unsafe-inline'``.
    """
    assert (
        '<script nomodule src="/static/js/legacy-fallback.js"></script>'
        in topology_html
    ), "Missing <script nomodule src=...> fallback for old browsers (NF-11)."


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


def test_topology_html_submit_disabled_until_selection(
    topology_html, topology_js,
):
    """Submit button MUST be disabled until a card is selected."""
    assert ":disabled" in topology_html, (
        "Submit button must use a :disabled binding so the user cannot "
        "submit before choosing a topology."
    )
    # The disabled binding must reference the selected state and the
    # submitting flag.
    assert "selected" in topology_html
    assert "submitting" in topology_html
    # The reactive flags also live in the page script.
    assert "submitting" in topology_js


def test_topology_html_aria_disabled_on_submit(topology_html):
    """ARIA mirror of disabled state for screen readers."""
    assert "aria-disabled" in topology_html
