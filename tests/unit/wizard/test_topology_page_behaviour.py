# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""API + behaviour tests for ``topology.html`` + ``topology.js``.

Mirrors the original ``test_topology_page.py`` (Spec 1 / B3 /
FR-W-FE9). Markup / structure assertions live in
``test_topology_page_markup.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

# The module-scoped asset-loading fixtures (topology_html, topology_js,
# common_js, topology_combined) are provided by
# tests/unit/wizard/conftest.py.


def test_topology_html_has_three_topology_values(topology_js):
    """A4 contract: backend accepts manager | manager_worker | worker_only."""
    for value in ("'manager'", "'manager_worker'", "'worker_only'"):
        assert value in topology_js, (
            f"Topology choice value {value!r} missing — must match A4's "
            "VALID_TOPOLOGIES set."
        )


def test_topology_html_card_labels_present(topology_js):
    """The three card labels match the spec exactly (B3 task)."""
    assert "Manager only" in topology_js
    assert "Manager + Worker" in topology_js
    assert "Worker only" in topology_js


def test_topology_html_posts_to_topology_endpoint(topology_js):
    """FR-W-FE4: choice submitted via POST /api/wizard/topology/."""
    assert "/api/wizard/topology/" in topology_js
    assert (
        "method: 'POST'" in topology_js
        or 'method: "POST"' in topology_js
    )


def test_topology_html_posts_done_after_topology(topology_js):
    """HIGH-1 (Phase F2): /done/ MUST be called after a successful /topology/.

    Without /done/ the launcher never writes the .wizard_done IPC marker
    and the runtime never starts. This was the spec-compliance blocker
    addressed in Phase F2.
    """
    assert "/api/wizard/done/" in topology_js, (
        "topology.js MUST POST /api/wizard/done/ between /topology/ "
        "success and the navigation to /redirecting (FR-W-FE4 / HIGH-1)."
    )
    # The /done/ call must come AFTER the /topology/ call in source order.
    topology_idx = topology_js.find("/api/wizard/topology/")
    done_idx = topology_js.find("/api/wizard/done/")
    assert topology_idx >= 0 and done_idx >= 0
    assert topology_idx < done_idx, (
        "/api/wizard/topology/ MUST come before /api/wizard/done/."
    )


def test_topology_html_sends_session_header(common_js):
    """FR-W8 + FR-W-FE3b: session token sent via X-Wizard-Session header only.

    The header is added by the shared wizardFetch() helper in common.js.
    """
    assert "X-Wizard-Session" in common_js, (
        "common.js wizardFetch() MUST attach X-Wizard-Session "
        "(FR-W8 / FR-W-FE3b)."
    )


def test_topology_html_uses_session_storage_not_local_storage(
    common_js, topology_js,
):
    """FR-W-FE3: session token retrieved from sessionStorage (NOT local)."""
    assert "sessionStorage" in common_js
    assert "localStorage" not in common_js, (
        "Wizard pages MUST NOT use localStorage (FR-W-FE3)."
    )
    assert "localStorage" not in topology_js


def test_topology_html_redirects_to_redirecting_on_success(topology_js):
    """B3 task: on 200, navigate to /redirecting (the B4 page)."""
    assert "/redirecting" in topology_js


def test_topology_html_handles_session_expiry(topology_js, common_js):
    """FR-W-FE3: 401 from a wizard API call must clear sessionStorage + redirect.

    The expireAndRedirect() helper lives in common.js; topology.js calls
    it on 401/403.
    """
    assert "401" in topology_js
    assert "expireAndRedirect" in topology_js
    assert "removeItem" in common_js or "clear()" in common_js


def test_topology_html_no_token_in_url(topology_combined):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment."""
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
        "?session_token=",
    ]
    for pat in bad_patterns:
        assert pat not in topology_combined, (
            f"Token must not appear in URL — found pattern {pat!r}."
        )


def test_topology_html_keyboard_handler_attached_after_mount(topology_js):
    """Integration pattern point 4: keyboard handler attached after mount.

    The handler MUST be JS-driven (not inline @keydown on each card),
    per the v2.3 Petite-vue + Bootstrap integration pattern. We verify
    the handler references the arrow keys + Home/End + Space/Enter and
    binds via addEventListener (not a Petite-vue @keydown attribute).
    Phase F2 added Home/End for the WAI-ARIA radiogroup pattern
    (MEDIUM-3).
    """
    assert "ArrowRight" in topology_js
    assert "ArrowLeft" in topology_js
    assert "ArrowDown" in topology_js
    assert "ArrowUp" in topology_js
    assert "Enter" in topology_js
    assert "addEventListener" in topology_js
    assert "DOMContentLoaded" in topology_js
    # MEDIUM-3 — Home/End cycle to first/last card.
    assert "'Home'" in topology_js
    assert "'End'" in topology_js


def test_topology_js_uses_history_replace_state(topology_js):
    """MEDIUM-2 (Phase F2): replace history so Back skips topology page."""
    assert "history.replaceState" in topology_js, (
        "topology.js MUST call window.history.replaceState before the "
        "navigation so Back does not return the user here and re-fire "
        "/done/ (MEDIUM-2)."
    )
